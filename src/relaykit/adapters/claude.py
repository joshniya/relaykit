"""Claude Code (``claude -p``). The reference adapter: hooks + live usage + preset session ids.

Stream (``--output-format stream-json --verbose``), one JSON object per line:
  {"type":"system","subtype":"init","session_id":...,"model":...,"tools":[...]}
  {"type":"assistant","message":{"content":[...],"usage":{input_tokens, cache_creation_input_tokens,
     cache_read_input_tokens, output_tokens}},"parent_tool_use_id":null}
  {"type":"rate_limit_event","rate_limit_info":{"status":"allowed|rejected","resetsAt":<epoch>,
     "rateLimitType":"five_hour|seven_day|..."}}
  {"type":"result","result":"<final text>","is_error":false,"total_cost_usd":...,
     "modelUsage":{"<model>":{"contextWindow":...}}}
Context = input + cache_creation + cache_read tokens of the latest main-thread assistant message.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from .. import procs
from .base import Adapter, Caps, Launch, RunSpec, Turn

HOOK_SCRIPT = Path(__file__).resolve().parent.parent / "_hook.py"


class ClaudeAdapter(Adapter):
    id = "claude"
    display = "Claude Code"
    binary = "claude"
    caps = Caps(hooks=True, live_usage=True, resume=True, preset_session_id=True, cost=True,
                structured_limits=True, tested=True)
    efforts = ("low", "medium", "high", "xhigh", "max")
    models_hint = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-haiku-4-5")
    install_hint = "https://code.claude.com/docs (native installer) or: npm i -g @anthropic-ai/claude-code"
    login_hint = "run `claude` once in a terminal and sign in (/login)"
    docs_url = "https://code.claude.com/docs/en/headless"

    def default_window(self, model: str) -> int:
        m = (model or "").lower()
        if "[1m]" in m:
            return 1_000_000
        if "haiku" in m:
            return 200_000
        return 200_000

    def hook_command(self, agent_id: str = "claude") -> str:
        py = procs.python_exe().replace("\\", "/")
        return f'"{py}" "{HOOK_SCRIPT.as_posix()}" {agent_id}'

    def settings_file(self, spec: RunSpec) -> Path:
        cmd = self.hook_command()
        entry = [{"matcher": "*", "hooks": [{"type": "command", "command": cmd, "timeout": 30}]}]
        settings = {"hooks": {
            "PreToolUse": entry,
            "PostToolUse": entry,
            "Stop": [{"hooks": [{"type": "command", "command": cmd, "timeout": 30}]}],
        }}
        path = spec.logs / "claude_settings.json"
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return path

    def build(self, spec: RunSpec) -> Launch:
        pre = self.command_prefix(spec.agent)
        if not pre:
            raise FileNotFoundError("claude CLI not found on PATH")
        a = spec.agent
        use_stdin = (a.prompt_via == "stdin") or (len(pre) == 1 and procs.is_batch(pre[0]))
        argv = list(pre) + ["-p"]
        if not use_stdin:
            argv.append(spec.prompt)
        sid = spec.session_id
        if spec.mode == "new":
            sid = sid or str(uuid.uuid4())
            argv += ["--session-id", sid]
        else:
            argv += ["--resume", sid]
        argv += ["--output-format", "stream-json", "--verbose", "--dangerously-skip-permissions",
                 "--disallowedTools", "AskUserQuestion", "--name", spec.label]
        if a.effort:
            argv += ["--effort", a.effort]
        if a.model:
            argv += ["--model", a.model]
        if spec.hooks:
            argv += ["--settings", str(self.settings_file(spec))]
        argv += [str(x) for x in (a.extra_args or [])]
        env = {
            # -p waits for background subagents/monitors only 10 idle minutes by default.
            "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "3600000",
        }
        env.update({k: str(v) for k, v in (a.env or {}).items()})
        return Launch(argv=argv, stdin=spec.prompt if use_stdin else None, env=env, session_id=sid)

    def on_line(self, line: str, turn: Turn) -> list:
        line = line.strip()
        if not line.startswith("{"):
            return []
        try:
            e = json.loads(line)
        except ValueError:
            return []
        t = e.get("type")
        out = []
        if t == "system" and e.get("subtype") == "init":
            turn.session_id = e.get("session_id") or turn.session_id
            turn.model = e.get("model") or turn.model
            out.append(("init", f"session init: model {turn.model}, {len(e.get('tools') or [])} tools, "
                                f"permission {e.get('permissionMode', '?')}"))
        elif t == "assistant":
            if e.get("parent_tool_use_id"):
                return []  # subagent traffic: not this session's context
            msg = e.get("message") or {}
            u = msg.get("usage") or {}
            ctx = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            if ctx > 0:
                turn.ctx_tokens = ctx
            for blk in msg.get("content") or []:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "text" and (blk.get("text") or "").strip():
                    turn.final_text = blk["text"]
                    out.append(("text", blk["text"]))
                elif blk.get("type") == "tool_use":
                    turn.tools += 1
                    out.append(("tool", f"{blk.get('name')} {self.hint(blk.get('input') or {}, Path('.'))}"))
        elif t == "rate_limit_event":
            info = e.get("rate_limit_info") or {}
            if info.get("status") == "rejected":
                turn.rate_limited = True
                try:
                    turn.reset_at = float(info.get("resetsAt")) if info.get("resetsAt") else None
                except (TypeError, ValueError):
                    turn.reset_at = None
                turn.limit_kind = str(info.get("rateLimitType") or "")
                out.append(("error", f"usage limit reached ({turn.limit_kind}); resets at {info.get('resetsAt')}"))
        elif t == "result":
            turn.result_seen = True
            turn.is_error = bool(e.get("is_error"))
            res = e.get("result")
            if isinstance(res, str) and res.strip():
                turn.final_text = res
            if turn.is_error and isinstance(res, str):
                turn.errors.append(res)
            try:
                turn.cost_usd = float(e.get("total_cost_usd") or 0.0)
            except (TypeError, ValueError):
                pass
            for _, mu in (e.get("modelUsage") or {}).items():
                if isinstance(mu, dict) and mu.get("contextWindow"):
                    used = int(mu.get("cacheReadInputTokens") or 0) + int(mu.get("cacheCreationInputTokens") or 0) + int(mu.get("inputTokens") or 0)
                    if used > 0:
                        turn.window = int(mu["contextWindow"])
        return out

    def probe_window(self, agent) -> int:
        """Ask the CLI for the model's context window with a one-line request (a few cents of quota)."""
        pre = self.command_prefix(agent)
        if not pre:
            return 0
        argv = pre + ["-p", "Reply with the single word OK.", "--output-format", "json",
                      "--no-session-persistence"]
        if agent.model:
            argv += ["--model", agent.model]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=180, encoding="utf-8",
                               errors="replace", env=procs.clean_env())
            data = json.loads(r.stdout.strip().splitlines()[-1])
            for _, mu in (data.get("modelUsage") or {}).items():
                if isinstance(mu, dict) and mu.get("contextWindow"):
                    return int(mu["contextWindow"])
        except Exception:
            return 0
        return 0
