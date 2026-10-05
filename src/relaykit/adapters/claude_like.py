"""CLIs whose headless stream is Claude-Code-shaped (system/init, assistant + message.usage, result):
Qwen Code, Amp and Cursor CLI. They reuse the Claude parser and only differ in how they launch.
All three are BETA: built from their documentation, not live-tested."""
from __future__ import annotations

import json

from .. import limits
from .base import Caps, Launch, RunSpec, Turn
from .claude import ClaudeAdapter


class QwenAdapter(ClaudeAdapter):
    id = "qwen"
    display = "Qwen Code"
    binary = "qwen"
    caps = Caps(hooks=False, live_usage=True, resume=True, preset_session_id=False, cost=False,
                structured_limits=False, tested=False)
    efforts = ()
    models_hint = ()
    install_hint = "npm i -g @qwen-code/qwen-code"
    login_hint = "run `qwen` once and sign in"
    docs_url = "https://github.com/QwenLM/qwen-code/blob/main/docs/users/features/headless.md"

    def default_window(self, model: str) -> int:
        return 256_000

    def build(self, spec: RunSpec) -> Launch:
        pre = self.command_prefix(spec.agent)
        if not pre:
            raise FileNotFoundError("qwen CLI not found on PATH")
        a = spec.agent
        argv = list(pre) + ["-p", spec.prompt, "--output-format", "stream-json", "--yolo"]
        if spec.mode != "new" and spec.session_id:
            argv += ["--resume", spec.session_id]
        if a.model:
            argv += ["--model", a.model]
        argv += [str(x) for x in (a.extra_args or [])]
        env = {"QWEN_CODE_UNATTENDED_RETRY": "0"}
        env.update({k: str(v) for k, v in (a.env or {}).items()})
        return Launch(argv=argv, env=env)

    def on_line(self, line: str, turn: Turn) -> list:
        out = super().on_line(line, turn)
        try:
            e = json.loads(line)
        except ValueError:
            return out
        if isinstance(e, dict) and e.get("type") == "system" and e.get("session_id"):
            turn.session_id = e["session_id"]
        return out

    def finish(self, spec, turn: Turn, exit_code, stderr: str) -> None:
        _finish_text_errors(turn, exit_code, stderr)


class AmpAdapter(ClaudeAdapter):
    id = "amp"
    display = "Amp"
    binary = "amp"
    caps = Caps(hooks=False, live_usage=True, resume=True, preset_session_id=False, cost=False,
                structured_limits=False, tested=False)
    efforts = ()
    models_hint = ()
    install_hint = "npm i -g @sourcegraph/amp"
    login_hint = "run `amp login` (or set AMP_API_KEY)"
    docs_url = "https://ampcode.com/manual"

    def build(self, spec: RunSpec) -> Launch:
        pre = self.command_prefix(spec.agent)
        if not pre:
            raise FileNotFoundError("amp CLI not found on PATH")
        a = spec.agent
        if spec.mode != "new" and spec.session_id:
            argv = list(pre) + ["threads", "continue", spec.session_id]
        else:
            argv = list(pre)
        argv += ["--dangerously-allow-all", "--stream-json"]
        argv += [str(x) for x in (a.extra_args or [])]
        argv += ["-x", spec.prompt]
        return Launch(argv=argv, env={k: str(v) for k, v in (a.env or {}).items()})

    def finish(self, spec, turn: Turn, exit_code, stderr: str) -> None:
        _finish_text_errors(turn, exit_code, stderr)


class CursorAdapter(ClaudeAdapter):
    id = "cursor"
    display = "Cursor CLI"
    binary = "cursor-agent"
    caps = Caps(hooks=False, live_usage=False, resume=True, preset_session_id=False, cost=False,
                structured_limits=False, tested=False)
    efforts = ()
    models_hint = ()
    install_hint = "curl https://cursor.com/install -fsS | bash"
    login_hint = "run `cursor-agent login` (or set CURSOR_API_KEY)"
    docs_url = "https://cursor.com/docs/cli/headless"

    def executable(self, agent):
        from .. import procs
        return procs.which(agent.binary or "cursor-agent") or procs.which("agent")

    def build(self, spec: RunSpec) -> Launch:
        pre = self.command_prefix(spec.agent)
        if not pre:
            raise FileNotFoundError("cursor-agent CLI not found on PATH")
        a = spec.agent
        argv = list(pre) + ["-p", spec.prompt, "--output-format", "stream-json", "--force", "--trust"]
        if spec.mode != "new" and spec.session_id:
            argv += ["--resume", spec.session_id]
        if a.model:
            argv += ["--model", a.model]
        argv += [str(x) for x in (a.extra_args or [])]
        return Launch(argv=argv, env={k: str(v) for k, v in (a.env or {}).items()})

    def on_line(self, line: str, turn: Turn) -> list:
        out = super().on_line(line, turn)
        try:
            e = json.loads(line)
        except ValueError:
            return out
        if isinstance(e, dict):
            if e.get("session_id"):
                turn.session_id = e["session_id"]
            u = e.get("usage") if e.get("type") == "result" else None
            if isinstance(u, dict):
                ctx = sum(int(u.get(k) or 0) for k in ("inputTokens", "cacheReadTokens", "cacheWriteTokens"))
                if ctx:
                    turn.ctx_tokens = ctx
        return out

    def finish(self, spec, turn: Turn, exit_code, stderr: str) -> None:
        _finish_text_errors(turn, exit_code, stderr)


def _finish_text_errors(turn: Turn, exit_code, stderr: str) -> None:
    if exit_code not in (0, None) and not turn.result_seen:
        turn.is_error = True
    blob = " ".join(turn.errors) + " " + (stderr or "")[-3000:]
    if (turn.is_error or not turn.result_seen) and limits.LIMIT.search(blob):
        turn.rate_limited = True
        turn.reset_at = turn.reset_at or limits.parse_reset(blob)
