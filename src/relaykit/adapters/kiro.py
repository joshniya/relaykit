"""Kiro CLI (``kiro-cli chat --output-format stream-json``). Beta: built from the docs and a probe.

Stream (ACP payloads), one JSON object per line:
  {"type":"runStarted","data":{...}}
  {"type":"metadata","data":{"sessionId":"..","contextUsagePercentage":12.3,...}}
  {"type":"sessionUpdate","data":{"sessionId":"..","update":{"sessionUpdate":"agent_message_chunk",
      "content":{"type":"text","text":".."}}}}
  {"type":"runFinished","data":{"sessionId":"..","status":"success","finalText":"..","finalTextTruncated":false}}

Kiro reports the context fill itself (contextUsagePercentage), so the supervisor can stop a run at
the hard limit; its postToolUse hooks can't inject text, so there are no mid-turn warnings and
the agent runs in checkpoint mode. Kiro's own auto-summary (~80%) starts a NEW session id; the
adapter follows the newest id it sees.
"""
from __future__ import annotations

import json

from .. import limits
from .base import Adapter, Caps, Launch, RunSpec, Turn


class KiroAdapter(Adapter):
    id = "kiro"
    display = "Kiro CLI"
    binary = "kiro-cli"
    caps = Caps(hooks=False, live_usage=True, resume=True, preset_session_id=False, cost=False,
                structured_limits=False, tested=False)
    install_hint = "https://kiro.dev/docs/cli/"
    login_hint = "run `kiro-cli login`"
    docs_url = "https://kiro.dev/docs/cli/headless/"

    def default_window(self, model: str) -> int:
        return 1_000_000 if "opus" in (model or "").lower() else 200_000

    def build(self, spec: RunSpec) -> Launch:
        pre = self.command_prefix(spec.agent)
        if not pre:
            raise FileNotFoundError("kiro-cli not found on PATH")
        a = spec.agent
        argv = list(pre) + ["chat", "--output-format", "stream-json", "--trust-all-tools"]
        if a.model:
            argv += ["--model", a.model]
        if spec.mode != "new" and spec.session_id:
            argv += ["--resume-id", spec.session_id]
        argv += [str(x) for x in (a.extra_args or [])]
        argv.append(spec.prompt)
        return Launch(argv=argv, stdin=None, env={k: str(v) for k, v in (a.env or {}).items()})

    def on_line(self, line: str, turn: Turn) -> list:
        line = line.strip()
        if not line.startswith("{"):
            if limits.classify(line)["limit"] or limits.classify(line)["auth"]:
                turn.errors.append(line)
            return []
        try:
            e = json.loads(line)
        except ValueError:
            return []
        t = e.get("type")
        d = e.get("data") or {}
        out = []
        if d.get("sessionId"):
            if turn.session_id and d["sessionId"] != turn.session_id:
                out.append(("info", f"session id changed (auto-summary): {d['sessionId']}"))
            turn.session_id = d["sessionId"]
        if t == "runStarted":
            out.append(("init", f"kiro run started (engine {d.get('engine', '?')})"))
        elif t == "metadata":
            if d.get("contextUsagePercentage") is not None:
                try:
                    turn.ctx_pct = float(d["contextUsagePercentage"])
                except (TypeError, ValueError):
                    pass
        elif t == "sessionUpdate":
            upd = d.get("update") or {}
            kind = upd.get("sessionUpdate")
            if kind == "agent_message_chunk":
                c = upd.get("content") or {}
                if c.get("type") == "text":
                    turn.extra["chunks"] = turn.extra.get("chunks", "") + (c.get("text") or "")
            elif kind in ("tool_call", "tool_call_update") and kind == "tool_call":
                turn.tools += 1
                out.append(("tool", str(upd.get("title") or upd.get("kind") or "tool")))
        elif t == "runFinished":
            turn.result_seen = True
            status = str(d.get("status") or "")
            turn.final_text = d.get("finalText") or turn.extra.get("chunks", "")
            if status and status != "success":
                turn.is_error = True
                turn.errors.append(f"runFinished status={status} stopReason={d.get('stopReason')}")
            if turn.final_text.strip():
                out.append(("text", turn.final_text))
        return out

    def finish(self, spec: RunSpec, turn: Turn, exit_code, stderr: str) -> None:
        if not turn.final_text:
            turn.final_text = turn.extra.get("chunks", "")
        blob = " ".join(turn.errors) + " " + (stderr or "")[-3000:]
        if limits.LIMIT.search(blob):
            turn.rate_limited = True
            turn.reset_at = limits.parse_reset(blob)
        if exit_code not in (0, None) and not turn.result_seen:
            turn.is_error = True
