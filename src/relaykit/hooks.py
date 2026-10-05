"""The context hook: one script, wired into each agent's own hook system for relay runs only.

It does nothing unless RELAYKIT_ACTIVE=1 (the supervisor sets it for the runs it starts), so it
is safe to leave installed. Configuration arrives in environment variables:

  RELAYKIT_ACTIVE=1              RELAYKIT_RELAY_DIR      RELAYKIT_PROGRESS
  RELAYKIT_HANDOFF_TOKENS        RELAYKIT_HARD_TOKENS    RELAYKIT_WINDOW
  RELAYKIT_STATE_DIR (logs)      RELAYKIT_RUN            RELAYKIT_AGENT

What it does (event names per agent in EVENT_MAP):
  after a tool   past the handoff point -> tell the agent to finish its step and hand off
  before a tool  past the hard point    -> refuse everything except reading/editing the relay files
  turn end       no marker on the last line -> refuse to stop ("keep going, or hand off")
  after a model call (Gemini)           -> record the prompt size (Gemini's only usage signal)

Any error -> allow everything (fail open): a broken hook must never wedge a relay.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Optional

MARKERS = ("RELAY HANDOFF", "RELAY COMPLETE", "RELAY BLOCKED", "RELAY CHECKPOINT")
TAIL_BYTES = 1_500_000
MAX_STOP_BLOCKS = 6

# per agent: logical event -> the agent's event name
EVENT_MAP = {
    "claude": {"post": "PostToolUse", "pre": "PreToolUse", "stop": "Stop"},
    "codex": {"post": "PostToolUse", "pre": "PreToolUse", "stop": "Stop"},
    "qwen": {"post": "PostToolUse", "pre": "PreToolUse", "stop": "Stop"},
    "copilot": {"post": "PostToolUse", "pre": "PreToolUse", "stop": "Stop"},
    "gemini": {"post": "AfterTool", "pre": "BeforeTool", "stop": "AfterAgent", "model": "AfterModel"},
}
EDIT_TOOLS = {"edit", "write", "multiedit", "notebookedit", "apply_patch", "write_file", "replace",
              "edit_file", "create_file", "str_replace", "fs_write"}
READ_TOOLS = {"read", "read_file", "view", "fs_read", "cat"}


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _tail_lines(path: str):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        start = max(0, size - TAIL_BYTES)
        fh.seek(start)
        data = fh.read()
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]
    for raw in reversed(lines):
        raw = raw.strip()
        if raw:
            try:
                yield json.loads(raw)
            except ValueError:
                continue


# ---------------------------------------------------------------- per-agent readers
def tokens_from_transcript(agent: str, path: str) -> int:
    if not path or not os.path.exists(path):
        return 0
    if agent == "codex":
        for e in _tail_lines(path):
            p = e.get("payload") or {}
            if p.get("type") == "token_count":
                last = (p.get("info") or {}).get("last_token_usage") or {}
                if last.get("total_tokens"):
                    return int(last["total_tokens"])
        return 0
    # claude-shaped transcripts (claude, qwen)
    for e in _tail_lines(path):
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        u = (e.get("message") or {}).get("usage") or {}
        total = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        if total > 0:
            return total
    return 0


def last_text(agent: str, data: dict) -> Optional[str]:
    for k in ("last_assistant_message", "prompt_response", "assistant_response", "response"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v
    path = data.get("transcript_path") or ""
    if agent in ("claude", "qwen") and path and os.path.exists(path):
        for e in _tail_lines(path):
            if e.get("type") != "assistant" or e.get("isSidechain"):
                continue
            content = (e.get("message") or {}).get("content") or []
            if isinstance(content, str):
                return content
            texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            if any(t.strip() for t in texts):
                return "\n".join(texts)
        return ""
    return None   # unknown: let the turn end (the supervisor handles a missing marker)


def _find_key(obj, key: str):
    if isinstance(obj, dict):
        if key in obj and isinstance(obj[key], (int, float)):
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


# ---------------------------------------------------------------- state + output
def _state_dir() -> str:
    return os.environ.get("RELAYKIT_STATE_DIR", "")


def write_context(agent: str, tokens: int, pct: float, event: str) -> None:
    d = _state_dir()
    if not d:
        return
    try:
        tmp = os.path.join(d, "context.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"agent": agent, "tokens": tokens, "pct": round(pct, 1), "event": event,
                       "run": int(_num("RELAYKIT_RUN", 0)), "ts": time.time()}, fh)
        os.replace(tmp, os.path.join(d, "context.json"))
    except Exception:
        pass


def read_context(agent: str) -> int:
    d = _state_dir()
    try:
        with open(os.path.join(d, "context.json"), encoding="utf-8") as fh:
            c = json.load(fh)
        if c.get("agent") == agent and int(c.get("run", -1)) == int(_num("RELAYKIT_RUN", 0)):
            return int(c.get("tokens") or 0)
    except Exception:
        pass
    return 0


def _stop_blocks(bump: bool) -> int:
    d = _state_dir()
    path = os.path.join(d, "stopgate.json") if d else ""
    run = int(_num("RELAYKIT_RUN", 0))
    n = 0
    try:
        with open(path, encoding="utf-8") as fh:
            s = json.load(fh)
        if s.get("run") == run:
            n = int(s.get("blocks") or 0)
    except Exception:
        pass
    if bump and path:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"run": run, "blocks": n + 1}, fh)
        except Exception:
            pass
    return n


def _inside_relay(tool_input) -> bool:
    relay = os.environ.get("RELAYKIT_RELAY_DIR", "")
    if not relay:
        return False
    blob = json.dumps(tool_input, ensure_ascii=False) if not isinstance(tool_input, str) else tool_input
    blob = blob.replace("\\\\", "/").replace("\\", "/").lower()
    rel = relay.replace("\\", "/").lower().rstrip("/")
    cands = {rel, os.path.basename(rel)}
    cwd = os.getcwd().replace("\\", "/").lower().rstrip("/")
    if rel.startswith(cwd + "/"):
        cands.add(rel[len(cwd) + 1:])
    return any(c and c in blob for c in cands)


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def run(agent: str, data: dict) -> Optional[dict]:
    """Decide; return the JSON to print (or None for 'allow, say nothing')."""
    ev = data.get("hook_event_name") or ""
    names = EVENT_MAP.get(agent, EVENT_MAP["claude"])
    window = int(_num("RELAYKIT_WINDOW", 200_000)) or 200_000
    handoff = int(_num("RELAYKIT_HANDOFF_TOKENS", window * 0.35))
    hard = max(int(_num("RELAYKIT_HARD_TOKENS", window * 0.45)), handoff + 1)
    progress = os.environ.get("RELAYKIT_PROGRESS", "RELAY_PROGRESS.md")
    gem = agent == "gemini"

    if gem and ev == names.get("model"):
        tok = _find_key(data, "promptTokenCount")
        if tok:
            write_context(agent, int(tok), 100.0 * tok / window, ev)
        return {}

    if agent == "gemini":
        tokens = read_context(agent)
    else:
        tokens = tokens_from_transcript(agent, data.get("transcript_path") or "")
    pct = 100.0 * tokens / window if tokens else 0.0

    if ev == names["stop"]:
        text = last_text(agent, data)
        if text is None:
            return {} if gem else None
        tail = ""
        for ln in reversed(text.splitlines()):
            if ln.strip():
                tail = ln.strip().strip("*_`> ").upper()
                break
        if any(tail.endswith(m) for m in MARKERS):
            return {} if gem else None
        if _stop_blocks(bump=False) >= MAX_STOP_BLOCKS:
            return {} if gem else None   # let the supervisor take over ("continue" / forced handoff)
        _stop_blocks(bump=True)
        if tokens >= handoff:
            why = (f"Your context is at {pct:.0f}% ({tokens:,} tokens), past the handoff point. Do the handoff now: "
                   f"save {progress} as RELAY_PROMPT.md describes, then end your reply with RELAY HANDOFF on the last line.")
        else:
            why = ("Relay rule: do not end your turn yet. Nobody is watching and nobody will answer. Continue with the "
                   "next action in the progress file. End a turn ONLY with one of these on the last line: RELAY HANDOFF "
                   "(progress saved; a fresh session continues), RELAY COMPLETE (the whole plan is done and the progress "
                   "file says so), RELAY BLOCKED (impossible without the owner), or RELAY CHECKPOINT if your start "
                   "prompt put you in checkpoint mode.")
        return {"decision": "deny", "reason": why} if gem else {"decision": "block", "reason": why}

    if tokens <= 0:
        return {} if gem else None
    if not gem:
        write_context(agent, tokens, pct, ev)

    if ev == names["pre"] and tokens >= hard:
        tool = str(data.get("tool_name") or "").lower()
        tin = data.get("tool_input") if "tool_input" in data else data.get("tool_args", {})
        if (tool in EDIT_TOOLS or tool in READ_TOOLS or "patch" in tool) and _inside_relay(tin):
            return {} if gem else None
        reason = (f"RELAY HARD LIMIT: context is at {pct:.0f}% ({tokens:,} tokens). Only the handoff is allowed now: "
                  f"update {progress} (Current, Next action, phase rows, files touched, a session-log line ending with "
                  f"'handoff'), then end your reply with RELAY HANDOFF on the last line. Every other tool is refused.")
        if gem:
            return {"decision": "deny", "reason": reason}
        return {"hookSpecificOutput": {"hookEventName": names["pre"], "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}

    if ev == names["post"] and tokens >= handoff:
        msg = (f"RELAY CONTEXT CHECK: this session's context is at {pct:.0f}% ({tokens:,} tokens), past the handoff "
               f"point ({100.0 * handoff / window:.0f}%). Finish only the small step you are on (a few more tool calls at "
               f"most; never start a new phase or a long operation), then hand off: save {progress} exactly as "
               f"RELAY_PROMPT.md describes and end your reply with RELAY HANDOFF on the last line. At "
               f"{100.0 * hard / window:.0f}% every tool except editing the relay files is refused.")
        return {"hookSpecificOutput": {"hookEventName": names["post"], "additionalContext": msg}}
    return {} if gem else None


def main(argv: list) -> int:
    agent = argv[1] if len(argv) > 1 else "claude"
    if os.environ.get("RELAYKIT_ACTIVE") != "1":
        if agent == "gemini":
            _emit({})
        return 0
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    try:
        out = run(agent, data)
    except Exception:
        out = {} if agent == "gemini" else None
    if out is not None:
        _emit(out)
    return 0
