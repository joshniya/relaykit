"""Any other agent CLI, described in config instead of code.

    [agents.myagent]
    adapter = "custom"
    command = ["myagent", "run", "--yes", "{prompt}"]                 # a new session
    resume_command = ["myagent", "run", "--yes", "--session", "{session}", "{prompt}"]   # optional
    prompt_via = "arg"           # or "stdin" (then leave {prompt} out of the command)
    context_window = 200000

Placeholders: {prompt}, {session}, {workdir}, {model}, {effort}. Output is read as plain text; the
LAST non-empty line must carry the marker. A line matching ``SESSION_ID=<id>`` (or a JSON object
with "session_id") sets the session id. Without usage data the agent runs in checkpoint mode, and
without a resume command every turn is a fresh session (the progress file carries the state).
"""
from __future__ import annotations

import json
import re

from .. import limits, procs
from .base import Adapter, Caps, Launch, RunSpec, Turn

_SID = re.compile(r"SESSION_ID=([A-Za-z0-9_.:-]+)")


class CustomAdapter(Adapter):
    id = "custom"
    display = "Custom CLI (from config)"
    caps = Caps(hooks=False, live_usage=False, resume=True, tested=False)

    def executable(self, agent):
        cmd = agent.command or []
        return procs.which(agent.binary or (cmd[0] if cmd else ""))

    def build(self, spec: RunSpec) -> Launch:
        a = spec.agent
        tpl = a.resume_command if (spec.mode != "new" and a.resume_command and spec.session_id) else a.command
        if not tpl:
            raise ValueError(f"agent {a.name!r}: set command = [...] for the custom adapter")
        vals = {"prompt": spec.prompt, "session": spec.session_id or "", "workdir": str(spec.workdir),
                "model": a.model or "", "effort": a.effort or ""}
        argv = []
        for part in tpl:
            s = str(part)
            for k, v in vals.items():
                s = s.replace("{" + k + "}", v)
            argv.append(s)
        exe = procs.which(argv[0]) or argv[0]
        argv[0] = exe
        if procs.is_batch(exe):
            from .base import resolve_npm_shim
            shim = resolve_npm_shim(exe)
            if shim:
                argv = shim + argv[1:]
        stdin = spec.prompt if a.prompt_via == "stdin" else None
        return Launch(argv=argv, stdin=stdin, env={k: str(v) for k, v in (a.env or {}).items()})

    def on_line(self, line: str, turn: Turn) -> list:
        s = line.rstrip()
        m = _SID.search(s)
        if m:
            turn.session_id = m.group(1)
        if s.startswith("{"):
            try:
                e = json.loads(s)
                if isinstance(e, dict) and e.get("session_id"):
                    turn.session_id = str(e["session_id"])
            except ValueError:
                pass
        if s.strip():
            turn.extra.setdefault("tail", []).append(s)
            turn.extra["tail"] = turn.extra["tail"][-400:]
        return [("text", s)] if s.strip() else []

    def finish(self, spec: RunSpec, turn: Turn, exit_code, stderr: str) -> None:
        turn.final_text = "\n".join(turn.extra.get("tail", []))
        turn.result_seen = exit_code == 0
        turn.is_error = exit_code not in (0, None)
        blob = turn.final_text[-3000:] + " " + (stderr or "")[-3000:]
        if turn.is_error and limits.LIMIT.search(blob):
            turn.rate_limited = True
            turn.reset_at = limits.parse_reset(blob)
