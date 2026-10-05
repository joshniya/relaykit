import json
import os
import time
from pathlib import Path

import pytest

from relaykit import hooks
from relaykit.adapters import RunSpec, Turn, get
from relaykit.config import AgentConfig


def spec(tmp_path, kind, mode="new", sid="", binary=None, **agent_kw):
    exe = tmp_path / ("x.exe" if os.name == "nt" else "x")
    exe.write_text("")
    a = AgentConfig(name=kind, binary=str(binary or exe), **agent_kw)
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    return RunSpec(mode=mode, prompt="PROMPT TEXT", session_id=sid, workdir=tmp_path, relay_dir=tmp_path / "r",
                   logs=logs, run_index=1, label="t", agent=a, hooks=True, window=100000)


# ---------------------------------------------------------------- build()
def test_claude_build_new_and_resume(tmp_path):
    ad = get("claude")
    l1 = ad.build(spec(tmp_path, "claude", model="m1", effort="high"))
    assert "--session-id" in l1.argv and l1.session_id and "PROMPT TEXT" in l1.argv
    assert l1.argv[l1.argv.index("--model") + 1] == "m1" and l1.argv[l1.argv.index("--effort") + 1] == "high"
    assert "--settings" in l1.argv and Path(l1.argv[l1.argv.index("--settings") + 1]).exists()
    settings = json.loads(Path(l1.argv[l1.argv.index("--settings") + 1]).read_text())
    assert set(settings["hooks"]) == {"PreToolUse", "PostToolUse", "Stop"}
    l2 = ad.build(spec(tmp_path, "claude", mode="resume", sid="abc"))
    assert l2.argv[l2.argv.index("--resume") + 1] == "abc"


def test_codex_build_puts_prompt_on_stdin(tmp_path):
    ad = get("codex")
    l1 = ad.build(spec(tmp_path, "codex", model="gpt-x", effort="high"))
    assert l1.argv[-1] == "-" and l1.stdin == "PROMPT TEXT"
    assert "exec" in l1.argv and "--json" in l1.argv and 'model_reasoning_effort="high"' in l1.argv
    l2 = ad.build(spec(tmp_path, "codex", mode="resume", sid="tid"))
    i = l2.argv.index("resume")
    assert l2.argv[i + 1] == "tid" and l2.argv[-1] == "-" and l2.argv.index("--json") < i


@pytest.mark.parametrize("kind", ["gemini", "kiro", "qwen", "amp", "cursor", "opencode", "copilot", "aider"])
def test_beta_adapters_build(tmp_path, kind):
    ad = get(kind)
    lnew = ad.build(spec(tmp_path, kind))
    assert lnew.argv and (("PROMPT TEXT" in " ".join(lnew.argv)) or lnew.stdin or kind == "aider")
    if ad.caps.resume:
        lres = ad.build(spec(tmp_path, kind, mode="resume", sid="S123"))
        assert any("S123" in a for a in lres.argv)


# ---------------------------------------------------------------- parsing
def test_claude_parse_stream():
    ad = get("claude")
    t = Turn()
    lines = [
        {"type": "system", "subtype": "init", "session_id": "s1", "model": "m"},
        {"type": "assistant", "parent_tool_use_id": None, "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}}],
                                                                     "usage": {"input_tokens": 5, "cache_read_input_tokens": 50000, "cache_creation_input_tokens": 100}}},
        {"type": "assistant", "parent_tool_use_id": "sub", "message": {"content": [], "usage": {"input_tokens": 999999}}},
        {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1800000000, "rateLimitType": "five_hour"}},
        {"type": "assistant", "parent_tool_use_id": None, "message": {"content": [{"type": "text", "text": "ok\nRELAY HANDOFF"}], "usage": {}}},
        {"type": "result", "is_error": False, "result": "ok\nRELAY HANDOFF", "total_cost_usd": 1.5,
         "modelUsage": {"m": {"contextWindow": 1000000, "cacheReadInputTokens": 1}}},
    ]
    for e in lines:
        ad.on_line(json.dumps(e), t)
    assert t.session_id == "s1" and t.ctx_tokens == 50105 and t.tools == 1
    assert t.rate_limited and t.reset_at == 1800000000 and t.limit_kind == "five_hour"
    assert t.final_text.endswith("RELAY HANDOFF") and t.cost_usd == 1.5 and t.window == 1000000


def test_codex_parse_stdout_and_rollout(tmp_path, monkeypatch):
    home = tmp_path / "codexhome"
    day = time.strftime("%Y/%m/%d")
    (home / "sessions" / day).mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    ad = get("codex")
    s = spec(tmp_path, "codex")
    t = Turn()
    ad.on_line(json.dumps({"type": "thread.started", "thread_id": "tid-1"}), t)
    ad.on_line(json.dumps({"type": "item.started", "item": {"type": "command_execution", "command": "ls"}}), t)
    ad.on_line(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done\nRELAY CHECKPOINT"}}), t)
    ad.on_line(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 999999}}), t)
    roll = home / "sessions" / day / "rollout-2026-01-01T00-00-00-tid-1.jsonl"
    roll.write_text(json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
        "last_token_usage": {"total_tokens": 73786}, "model_context_window": 258400},
        "rate_limits": {"primary": {"used_percent": 100.0, "resets_at": 1791107172}, "secondary": {"used_percent": 5, "resets_at": 1791637020},
                        "rate_limit_reached_type": "rate_limit_reached"}}}) + "\n", encoding="utf-8")
    ad.poll(s, t)
    assert t.session_id == "tid-1" and t.tools == 1 and t.result_seen
    assert t.ctx_tokens == 73786 and t.window == 258400
    assert t.rate_limited and t.reset_at == 1791107172
    assert t.final_text.endswith("RELAY CHECKPOINT")


def test_codex_resume_ignores_old_rate_limits(tmp_path, monkeypatch):
    home = tmp_path / "codexhome"
    day = time.strftime("%Y/%m/%d")
    (home / "sessions" / day).mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    roll = home / "sessions" / day / "rollout-x-tid-2.jsonl"
    roll.write_text(json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
        "last_token_usage": {"total_tokens": 5000}, "model_context_window": 258400},
        "rate_limits": {"rate_limit_reached_type": "rate_limit_reached", "primary": {"used_percent": 100, "resets_at": 1}}}}) + "\n")
    ad = get("codex")
    t = Turn(session_id="tid-2")
    ad.poll(spec(tmp_path, "codex", mode="resume", sid="tid-2"), t)
    assert t.ctx_tokens == 5000 and not t.rate_limited


def test_kiro_parse():
    ad = get("kiro")
    t = Turn()
    for e in [{"type": "metadata", "data": {"sessionId": "k1", "contextUsagePercentage": 41.5}},
              {"type": "sessionUpdate", "data": {"sessionId": "k1", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hi "}}}},
              {"type": "runFinished", "data": {"sessionId": "k2", "status": "success", "finalText": "all good\nRELAY HANDOFF"}}]:
        ad.on_line(json.dumps(e), t)
    assert t.session_id == "k2" and t.ctx_pct == 41.5 and t.pct(1000) == 41.5 and t.final_text.endswith("RELAY HANDOFF")


def test_gemini_parse_final_text_after_last_tool(tmp_path):
    ad = get("gemini")
    t = Turn()
    for e in [{"type": "init", "session_id": "g1", "model": "gemini-x"},
              {"type": "message", "role": "assistant", "content": "thinking out loud", "delta": True},
              {"type": "tool_use", "tool_name": "read_file", "parameters": {"file_path": "a"}},
              {"type": "message", "role": "assistant", "content": "done.\n", "delta": True},
              {"type": "message", "role": "assistant", "content": "RELAY HANDOFF", "delta": True},
              {"type": "result", "status": "success", "stats": {}}]:
        ad.on_line(json.dumps(e), t)
    ad.finish(spec(tmp_path, "gemini"), t, 0, "")
    assert t.session_id == "g1" and t.final_text == "done.\nRELAY HANDOFF" and t.tools == 1


# ---------------------------------------------------------------- hooks
@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    relay = tmp_path / "relay"
    relay.mkdir()
    monkeypatch.setenv("RELAYKIT_ACTIVE", "1")
    monkeypatch.setenv("RELAYKIT_RELAY_DIR", str(relay))
    monkeypatch.setenv("RELAYKIT_WINDOW", "100000")
    monkeypatch.setenv("RELAYKIT_HANDOFF_TOKENS", "35000")
    monkeypatch.setenv("RELAYKIT_HARD_TOKENS", "45000")
    monkeypatch.setenv("RELAYKIT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("RELAYKIT_RUN", "1")
    return relay


def claude_transcript(path: Path, tokens: int, text: str = "") -> str:
    lines = [{"type": "assistant", "message": {"usage": {"input_tokens": tokens}, "content": [{"type": "text", "text": text}] if text else []}}]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return str(path)


def test_hook_post_tool_warns_past_handoff(tmp_path, hook_env):
    tr = claude_transcript(tmp_path / "t.jsonl", 36000)
    out = hooks.run("claude", {"hook_event_name": "PostToolUse", "transcript_path": tr})
    assert "RELAY CONTEXT CHECK" in out["hookSpecificOutput"]["additionalContext"]
    tr = claude_transcript(tmp_path / "t.jsonl", 20000)
    assert hooks.run("claude", {"hook_event_name": "PostToolUse", "transcript_path": tr}) is None


def test_hook_pre_tool_denies_past_hard_except_relay_files(tmp_path, hook_env):
    tr = claude_transcript(tmp_path / "t.jsonl", 46000)
    deny = hooks.run("claude", {"hook_event_name": "PreToolUse", "transcript_path": tr, "tool_name": "Bash", "tool_input": {"command": "pytest"}})
    assert deny["hookSpecificOutput"]["permissionDecision"] == "deny"
    allow = hooks.run("claude", {"hook_event_name": "PreToolUse", "transcript_path": tr, "tool_name": "Edit",
                                 "tool_input": {"file_path": str(hook_env / "RELAY_PROGRESS.md")}})
    assert allow is None


def test_hook_stop_gate(tmp_path, hook_env):
    tr = claude_transcript(tmp_path / "t.jsonl", 1000, "I fixed it.")
    out = hooks.run("claude", {"hook_event_name": "Stop", "transcript_path": tr})
    assert out["decision"] == "block"
    tr = claude_transcript(tmp_path / "t.jsonl", 1000, "saved\nRELAY HANDOFF")
    assert hooks.run("claude", {"hook_event_name": "Stop", "transcript_path": tr}) is None
    # codex passes the text directly
    assert hooks.run("codex", {"hook_event_name": "Stop", "last_assistant_message": "x\nRELAY COMPLETE"}) is None
    # after MAX_STOP_BLOCKS refusals in one run, let it stop (the supervisor takes over)
    tr = claude_transcript(tmp_path / "t.jsonl", 1000, "no marker")
    results = [hooks.run("claude", {"hook_event_name": "Stop", "transcript_path": tr}) for _ in range(10)]
    assert results[-1] is None


def test_hook_gemini_records_usage_and_uses_its_shapes(tmp_path, hook_env):
    assert hooks.run("gemini", {"hook_event_name": "AfterModel", "llm_response": {"usageMetadata": {"promptTokenCount": 47000}}}) == {}
    ctx = json.loads((tmp_path / "context.json").read_text())
    assert ctx["tokens"] == 47000 and ctx["agent"] == "gemini"
    deny = hooks.run("gemini", {"hook_event_name": "BeforeTool", "tool_name": "run_shell_command", "tool_input": {"command": "ls"}})
    assert deny["decision"] == "deny"
    warn = hooks.run("gemini", {"hook_event_name": "AfterTool"})
    assert "additionalContext" in warn["hookSpecificOutput"]
    stop = hooks.run("gemini", {"hook_event_name": "AfterAgent", "prompt_response": "done, no marker"})
    assert stop["decision"] == "deny"


def test_hook_inactive_outside_relays(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("RELAYKIT_ACTIVE", raising=False)
    assert hooks.main(["hook", "claude"]) == 0
    assert capsys.readouterr().out == ""
