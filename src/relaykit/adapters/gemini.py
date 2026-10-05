"""Google Gemini CLI (``gemini -p ... -o stream-json``). BETA: built from the docs and source.

Stream, one JSON object per line:
  {"type":"init","session_id":"..","model":".."}
  {"type":"message","role":"assistant","content":"..","delta":true}
  {"type":"tool_use","tool_name":"..","tool_id":"..","parameters":{}}
  {"type":"tool_result","tool_id":"..","status":"success","output":".."}
  {"type":"error","severity":"warning|error","message":".."}
  {"type":"result","status":"success","stats":{...}}     <- stats are run TOTALS, not context fill
The final text is NOT repeated in ``result``: it's the assistant messages after the last tool call.

Context fill is only visible to Gemini's hooks (AfterModel's per-call usageMetadata). Once
``relaykit hooks install gemini`` has added relaykit's hooks to ``.gemini/settings.json`` (they do
nothing outside a relay), the hook reports usage to the supervisor and enforces the limits
mid-turn like Claude's. Without them Gemini runs in checkpoint mode.
Exit codes: 41 auth, 42 bad input, 53 turn limit, 55 untrusted folder.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import limits
from .base import Adapter, Caps, Launch, RunSpec, Turn


def hooks_installed(workdir: Path) -> bool:
    for p in (Path(workdir) / ".gemini" / "settings.json", Path.home() / ".gemini" / "settings.json"):
        try:
            if "relaykit" in p.read_text(encoding="utf-8") and "_hook.py" in p.read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


class GeminiAdapter(Adapter):
    id = "gemini"
    display = "Gemini CLI"
    binary = "gemini"
    caps = Caps(hooks=False, live_usage=False, resume=True, preset_session_id=False, cost=False,
                structured_limits=False, tested=False)
    models_hint = ("gemini-3-pro", "gemini-3-flash", "gemini-2.5-pro")
    install_hint = "npm i -g @google/gemini-cli"
    login_hint = "run `gemini` once and sign in with Google (or set GEMINI_API_KEY)"
    docs_url = "https://geminicli.com/docs/cli/headless/"

    def default_window(self, model: str) -> int:
        return 256_000 if "gemma" in (model or "").lower() else 1_048_576

    def hooks_ready(self, workdir: Path) -> bool:
        return hooks_installed(workdir)

    def build(self, spec: RunSpec) -> Launch:
        pre = self.command_prefix(spec.agent)
        if not pre:
            raise FileNotFoundError("gemini CLI not found on PATH")
        a = spec.agent
        argv = list(pre) + ["-p", spec.prompt, "-o", "stream-json", "--approval-mode=yolo", "--skip-trust"]
        if spec.mode != "new" and spec.session_id:
            argv += ["-r", spec.session_id]
        if a.model:
            argv += ["-m", a.model]
        argv += [str(x) for x in (a.extra_args or [])]
        env = {"GEMINI_CLI_TRUST_WORKSPACE": "true"}
        env.update({k: str(v) for k, v in (a.env or {}).items()})
        return Launch(argv=argv, env=env)

    def on_line(self, line: str, turn: Turn) -> list:
        line = line.strip()
        if not line.startswith("{"):
            if line:
                turn.extra.setdefault("raw", []).append(line)
            return []
        try:
            e = json.loads(line)
        except ValueError:
            return []
        t = e.get("type")
        out = []
        if t == "init":
            turn.session_id = e.get("session_id") or turn.session_id
            turn.model = e.get("model") or turn.model
            out.append(("init", f"session {turn.session_id}, model {turn.model}"))
        elif t == "message" and e.get("role") == "assistant":
            content = e.get("content") or ""
            if e.get("delta"):
                turn.extra["cur"] = turn.extra.get("cur", "") + content
            else:
                turn.extra["cur"] = content
        elif t == "tool_use":
            if turn.extra.get("cur", "").strip():
                out.append(("text", turn.extra["cur"]))
            turn.extra["cur"] = ""
            turn.tools += 1
            params = e.get("parameters") or {}
            out.append(("tool", f"{e.get('tool_name')} {self.hint(params, Path('.'))}"))
        elif t == "error":
            msg = str(e.get("message") or "")
            if e.get("severity") != "warning":
                turn.errors.append(msg)
            out.append(("error", msg))
        elif t == "result":
            turn.result_seen = True
            if str(e.get("status") or "success") != "success":
                turn.is_error = True
                err = e.get("error") or {}
                turn.errors.append(str(err.get("message") or e.get("status")))
        return out

    def finish(self, spec: RunSpec, turn: Turn, exit_code, stderr: str) -> None:
        turn.final_text = turn.extra.get("cur", "") or turn.final_text
        if turn.final_text.strip():
            pass
        if exit_code == 41:
            turn.errors.append("authentication required (exit 41)")
            turn.is_error = True
        elif exit_code not in (0, None):
            turn.is_error = True
        blob = " ".join(turn.errors) + " " + (stderr or "")[-3000:] + " " + " ".join(turn.extra.get("raw", [])[-20:])
        if limits.LIMIT.search(blob) or "exhausted your" in blob.lower():
            turn.rate_limited = True
            turn.reset_at = limits.parse_reset(blob)
        # usage reported by relaykit's AfterModel hook (if installed)
        try:
            ctx = json.loads((spec.logs / "context.json").read_text(encoding="utf-8"))
            if ctx.get("agent") == "gemini" and ctx.get("tokens"):
                turn.ctx_tokens = int(ctx["tokens"])
        except Exception:
            pass

    def poll(self, spec: RunSpec, turn: Turn) -> list:
        try:
            ctx = json.loads((spec.logs / "context.json").read_text(encoding="utf-8"))
            if ctx.get("agent") == "gemini" and ctx.get("tokens") and ctx.get("run") == spec.run_index:
                turn.ctx_tokens = int(ctx["tokens"])
        except Exception:
            pass
        return []
