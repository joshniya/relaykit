"""More agent CLIs, all BETA (built from their documentation, not live-tested):
opencode, GitHub Copilot CLI and aider."""
from __future__ import annotations

import json
from pathlib import Path

from .. import limits
from .base import Adapter, Caps, Launch, RunSpec, Turn


class OpencodeAdapter(Adapter):
    """``opencode run --format json``. Events carry ``sessionID``; ``text`` parts hold the reply;
    ``step_finish`` carries per-step tokens (input + cache.read + cache.write = context fill)."""
    id = "opencode"
    display = "opencode"
    binary = "opencode"
    caps = Caps(hooks=False, live_usage=True, resume=True, tested=False)
    install_hint = "npm i -g opencode-ai  (or: curl -fsSL https://opencode.ai/install | bash)"
    login_hint = "run `opencode auth login`"
    docs_url = "https://opencode.ai/docs/cli/"

    def build(self, spec: RunSpec) -> Launch:
        pre = self.command_prefix(spec.agent)
        if not pre:
            raise FileNotFoundError("opencode not found on PATH")
        a = spec.agent
        argv = list(pre) + ["run", "--format", "json"]
        if spec.mode != "new" and spec.session_id:
            argv += ["-s", spec.session_id]
        if a.model:
            argv += ["-m", a.model]
        argv += [str(x) for x in (a.extra_args or [])]
        argv.append(spec.prompt)
        env = {"OPENCODE_DISABLE_AUTOCOMPACT": "1", "OPENCODE_PERMISSION": '{"*":"allow"}'}
        env.update({k: str(v) for k, v in (a.env or {}).items()})
        return Launch(argv=argv, env=env)

    def on_line(self, line: str, turn: Turn) -> list:
        try:
            e = json.loads(line)
        except ValueError:
            return []
        if not isinstance(e, dict):
            return []
        out = []
        sid = e.get("sessionID") or (e.get("part") or {}).get("sessionID")
        if sid:
            turn.session_id = sid
        t = e.get("type")
        part = e.get("part") or {}
        if t == "text" and part.get("text"):
            turn.final_text = part["text"]
            out.append(("text", part["text"]))
        elif t in ("tool_use", "tool"):
            turn.tools += 1
            out.append(("tool", str(part.get("tool") or part.get("name") or "tool")))
        elif t == "step_finish":
            tok = part.get("tokens") or {}
            cache = tok.get("cache") or {}
            ctx = int(tok.get("input") or 0) + int(cache.get("read") or 0) + int(cache.get("write") or 0)
            if ctx:
                turn.ctx_tokens = ctx
            try:
                turn.cost_usd += float(part.get("cost") or 0)
            except (TypeError, ValueError):
                pass
        elif t == "error":
            err = e.get("error") or {}
            data = err.get("data") or {}
            msg = f"{err.get('name', 'error')}: {data.get('message', '')} (status {data.get('statusCode')})"
            turn.errors.append(msg)
            if data.get("statusCode") == 429:
                turn.rate_limited = True
            out.append(("error", msg))
        return out

    def finish(self, spec, turn: Turn, exit_code, stderr: str) -> None:
        turn.result_seen = exit_code == 0
        turn.is_error = exit_code not in (0, None)
        blob = " ".join(turn.errors) + " " + (stderr or "")[-3000:]
        if turn.rate_limited or (turn.is_error and limits.LIMIT.search(blob)):
            turn.rate_limited = True
            turn.reset_at = limits.parse_reset(blob)


class CopilotAdapter(Adapter):
    """GitHub Copilot CLI (``copilot -p``). Final answer = ``assistant.message`` with
    ``phase == "final_answer"``; ``session.usage_info`` (when present) gives currentTokens/tokenLimit."""
    id = "copilot"
    display = "GitHub Copilot CLI"
    binary = "copilot"
    caps = Caps(hooks=False, live_usage=True, resume=True, tested=False)
    install_hint = "npm i -g @github/copilot"
    login_hint = "run `copilot` and /login"
    docs_url = "https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference"

    def build(self, spec: RunSpec) -> Launch:
        pre = self.command_prefix(spec.agent)
        if not pre:
            raise FileNotFoundError("copilot CLI not found on PATH")
        a = spec.agent
        argv = list(pre) + ["-p", spec.prompt, "--allow-all-tools", "--no-ask-user", "-s", "--output-format", "json"]
        if spec.mode != "new" and spec.session_id:
            argv += [f"--resume={spec.session_id}"]
        if a.model:
            argv += ["--model", a.model]
        argv += [str(x) for x in (a.extra_args or [])]
        return Launch(argv=argv, env={k: str(v) for k, v in (a.env or {}).items()})

    def on_line(self, line: str, turn: Turn) -> list:
        try:
            e = json.loads(line)
        except ValueError:
            s = line.strip()
            if s:
                turn.extra.setdefault("raw", []).append(s)
            return []
        if not isinstance(e, dict):
            return []
        out = []
        if e.get("sessionId"):
            turn.session_id = e["sessionId"]
        t = e.get("type") or ""
        data = e.get("data") or {}
        if t == "assistant.message":
            txt = data.get("content") or data.get("text") or ""
            if txt:
                turn.final_text = txt if data.get("phase") in (None, "final_answer") else turn.final_text
                out.append(("text", txt))
        elif t.startswith("tool.") and t.endswith("start"):
            turn.tools += 1
            out.append(("tool", str(data.get("toolName") or data.get("name") or "tool")))
        elif t == "session.usage_info":
            if data.get("currentTokens"):
                turn.ctx_tokens = int(data["currentTokens"])
            if data.get("tokenLimit"):
                turn.window = int(data["tokenLimit"])
        elif t == "result":
            turn.result_seen = True
            if e.get("exitCode") not in (0, None):
                turn.is_error = True
        elif "error" in t:
            msg = str(data.get("message") or e)
            turn.errors.append(msg)
            out.append(("error", msg))
        return out

    def finish(self, spec, turn: Turn, exit_code, stderr: str) -> None:
        if not turn.final_text and turn.extra.get("raw"):
            turn.final_text = "\n".join(turn.extra["raw"][-50:])
        if exit_code not in (0, None):
            turn.is_error = True
        blob = " ".join(turn.errors) + " " + (stderr or "")[-3000:]
        if turn.is_error and limits.LIMIT.search(blob):
            turn.rate_limited = True
            turn.reset_at = limits.parse_reset(blob)


class AiderAdapter(Adapter):
    """aider (``aider --message``): plain text, no sessions, no usage. Every turn is a fresh
    process; the progress file carries the state. relaykit's git mode is recommended instead of
    aider's own auto-commits, so they are switched off by default."""
    id = "aider"
    display = "aider"
    binary = "aider"
    caps = Caps(hooks=False, live_usage=False, resume=False, tested=False)
    install_hint = "pipx install aider-chat"
    login_hint = "set your model provider's API key (e.g. ANTHROPIC_API_KEY / OPENAI_API_KEY)"
    docs_url = "https://aider.chat/docs/scripting.html"

    def build(self, spec: RunSpec) -> Launch:
        pre = self.command_prefix(spec.agent)
        if not pre:
            raise FileNotFoundError("aider not found on PATH")
        a = spec.agent
        msg_file = spec.logs / f"run_{spec.run_index:03d}.message.md"
        msg_file.write_text(spec.prompt, encoding="utf-8")
        argv = list(pre) + ["--message-file", str(msg_file), "--yes-always", "--no-auto-commits",
                            "--no-pretty", "--no-stream"]
        for f in ("AGENTS.md", ".relaykit/PROJECT.md"):
            if (Path(spec.workdir) / f).exists():
                argv += ["--read", f]
        if a.model:
            argv += ["--model", a.model]
        argv += [str(x) for x in (a.extra_args or [])]
        return Launch(argv=argv, env={k: str(v) for k, v in (a.env or {}).items()})

    def on_line(self, line: str, turn: Turn) -> list:
        s = line.rstrip()
        if s.strip():
            tail = turn.extra.setdefault("tail", [])
            tail.append(s)
            del tail[:-300]
            return [("text", s)]
        return []

    def finish(self, spec, turn: Turn, exit_code, stderr: str) -> None:
        turn.final_text = "\n".join(turn.extra.get("tail", []))
        turn.result_seen = exit_code == 0
        turn.is_error = exit_code not in (0, None)
        blob = turn.final_text[-3000:] + " " + (stderr or "")[-3000:]
        if turn.is_error and limits.LIMIT.search(blob):
            turn.rate_limited = True
            turn.reset_at = limits.parse_reset(blob)
