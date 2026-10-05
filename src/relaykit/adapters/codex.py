"""OpenAI Codex CLI (``codex exec``).

Stdout (``--json``), one event per line:
  {"type":"thread.started","thread_id":"<uuid>"}
  {"type":"item.completed","item":{"type":"agent_message","text":"..."}}     (also command_execution,
     file_change, mcp_tool_call, web_search, reasoning, todo_list, error)
  {"type":"turn.completed","usage":{...}}     <- running total for the THREAD, not context fill
  {"type":"error","message":"..."} / {"type":"turn.failed","error":{"message":"..."}}

Context fill and rate limits are NOT in stdout. They are in the session's rollout file,
``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<thread_id>.jsonl``:
  {"type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"total_tokens":N},
     "model_context_window":W},"rate_limits":{"primary":{"used_percent":..,"resets_at":<epoch>},
     "secondary":{...},"rate_limit_reached_type":null|"rate_limit_reached"|...}}}
so the adapter tails that file while the run is alive (live usage + structured limits).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from .. import limits, tomlio
from .base import Adapter, Caps, Launch, RunSpec, Turn


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


class CodexAdapter(Adapter):
    id = "codex"
    display = "OpenAI Codex CLI"
    binary = "codex"
    caps = Caps(hooks=False, live_usage=True, resume=True, preset_session_id=False, cost=False,
                structured_limits=True, tested=True)
    efforts = ("minimal", "low", "medium", "high", "xhigh", "max")
    install_hint = "npm i -g @openai/codex  (or: brew install codex)"
    login_hint = "run `codex login`"
    docs_url = "https://developers.openai.com/codex/noninteractive"

    # ---- discovery
    def models(self) -> list:
        try:
            data = json.loads((codex_home() / "models_cache.json").read_text(encoding="utf-8"))
        except Exception:
            return []
        out = []
        for m in data.get("models") or []:
            if isinstance(m, dict) and m.get("visibility", "list") == "list":
                out.append({
                    "id": m.get("slug"),
                    "window": m.get("context_window"),
                    "efforts": [e.get("effort") for e in (m.get("supported_reasoning_levels") or []) if isinstance(e, dict)],
                    "default_effort": m.get("default_reasoning_level"),
                })
        return out

    def configured_default(self) -> tuple:
        try:
            cfg = tomlio.load_file(codex_home() / "config.toml")
            return cfg.get("model", ""), cfg.get("model_reasoning_effort", "")
        except Exception:
            return "", ""

    def default_window(self, model: str) -> int:
        for m in self.models():
            if m["id"] == model and m.get("window"):
                return int(m["window"] * 0.95)
        return 258_400

    # ---- one run
    def build(self, spec: RunSpec) -> Launch:
        pre = self.command_prefix(spec.agent)
        if not pre:
            raise FileNotFoundError("codex CLI not found on PATH")
        a = spec.agent
        last_msg = spec.logs / f"run_{spec.run_index:03d}.last.txt"
        common = ["--json", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check",
                  "-o", str(last_msg)]
        if a.model:
            common += ["-m", a.model]
        if a.effort:
            common += ["-c", f'model_reasoning_effort="{a.effort}"']
        common += [str(x) for x in (a.extra_args or [])]
        # The prompt goes through stdin ("-"): no quoting surprises on any shell or OS.
        if spec.mode == "new":
            argv = list(pre) + ["exec"] + common + ["-C", str(spec.workdir), "-"]
        else:
            argv = list(pre) + ["exec"] + common + ["resume", spec.session_id, "-"]
        env = {k: str(v) for k, v in (a.env or {}).items()}
        return Launch(argv=argv, stdin=spec.prompt, env=env, session_id=spec.session_id if spec.mode != "new" else "")

    def on_line(self, line: str, turn: Turn) -> list:
        line = line.strip()
        if not line.startswith("{"):
            if line.lower().startswith("error") or "usage limit" in line.lower():
                turn.errors.append(line)
            return []
        try:
            e = json.loads(line)
        except ValueError:
            return []
        t = e.get("type")
        out = []
        if t == "thread.started":
            turn.session_id = e.get("thread_id") or turn.session_id
            turn.extra["started"] = time.time()
            out.append(("init", f"thread {turn.session_id}"))
        elif t in ("item.completed", "item.started"):
            item = e.get("item") or {}
            it = item.get("type")
            if t == "item.completed" and it == "agent_message" and (item.get("text") or "").strip():
                turn.final_text = item["text"]
                out.append(("text", item["text"]))
            elif t == "item.started" and it in ("command_execution", "file_change", "mcp_tool_call", "web_search"):
                turn.tools += 1
                if it == "command_execution":
                    out.append(("tool", f"shell {item.get('command', '')}"))
                elif it == "file_change":
                    paths = ", ".join(c.get("path", "") for c in (item.get("changes") or []) if isinstance(c, dict))
                    out.append(("tool", f"edit {paths}"))
                elif it == "mcp_tool_call":
                    out.append(("tool", f"mcp {item.get('server')}.{item.get('tool')}"))
                else:
                    out.append(("tool", f"web_search {item.get('query', '')}"))
            elif t == "item.completed" and it == "error":
                turn.errors.append(str(item.get("message", "")))
        elif t == "turn.completed":
            turn.result_seen = True
        elif t in ("error", "turn.failed"):
            msg = e.get("message") or (e.get("error") or {}).get("message") or json.dumps(e)
            turn.errors.append(str(msg))
            if t == "turn.failed":
                turn.result_seen = True
                turn.is_error = True
            c = limits.classify(str(msg))
            if c["limit"]:
                turn.rate_limited = True
                turn.reset_at = turn.reset_at or limits.parse_reset(str(msg))
            out.append(("error", str(msg)))
        return out

    # ---- side channel: the rollout file
    def _find_rollout(self, thread_id: str) -> Optional[Path]:
        if not thread_id:
            return None
        base = codex_home() / "sessions"
        days = {time.strftime("%Y/%m/%d", time.localtime(time.time() - d * 86400)) for d in (0, 1)}
        for day in sorted(days, reverse=True):
            hits = list((base / day).glob(f"rollout-*-{thread_id}.jsonl"))
            if hits:
                return hits[0]
        hits = list(base.glob(f"*/*/*/rollout-*-{thread_id}.jsonl"))
        return hits[0] if hits else None

    def poll(self, spec: RunSpec, turn: Turn) -> list:
        path = turn.extra.get("rollout")
        if not path:
            found = self._find_rollout(turn.session_id)
            if not found:
                return []
            path = turn.extra["rollout"] = str(found)
            turn.extra["rollout_pos"] = 0
            if spec.mode != "new":
                # A resumed thread: earlier runs' events are history. Take only the latest usage from
                # them (never their rate-limit flags) and read live events from the current end.
                try:
                    data = found.read_bytes()
                    self._apply(data, turn, limits_too=False)
                    turn.extra["rollout_pos"] = data.rfind(b"\n") + 1
                except OSError:
                    pass
        pos = turn.extra.get("rollout_pos", 0)
        try:
            with open(path, "rb") as fh:
                fh.seek(pos)
                chunk = fh.read()
        except OSError:
            return []
        nl = chunk.rfind(b"\n")
        if nl < 0:
            return []
        turn.extra["rollout_pos"] = pos + nl + 1
        self._apply(chunk[: nl + 1], turn, limits_too=True)
        return []

    @staticmethod
    def _apply(data: bytes, turn: Turn, limits_too: bool) -> None:
        for raw in data.splitlines():
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            p = e.get("payload") or {}
            if p.get("type") != "token_count":
                continue
            info = p.get("info") or {}
            last = info.get("last_token_usage") or {}
            if last.get("total_tokens"):
                turn.ctx_tokens = int(last["total_tokens"])
            if info.get("model_context_window"):
                turn.window = int(info["model_context_window"])
            rl = p.get("rate_limits") or {}
            if limits_too and rl.get("rate_limit_reached_type"):
                turn.rate_limited = True
                turn.limit_kind = str(rl["rate_limit_reached_type"])
                windows = [w for w in (rl.get("primary"), rl.get("secondary")) if isinstance(w, dict)]
                full = [w for w in windows if float(w.get("used_percent") or 0) >= 99.5 and w.get("resets_at")]
                pick = full or [w for w in windows if w.get("resets_at")]
                if pick:
                    turn.reset_at = max(float(w["resets_at"]) for w in pick)

    def finish(self, spec: RunSpec, turn: Turn, exit_code, stderr: str) -> None:
        self.poll(spec, turn)
        if not turn.final_text:
            p = spec.logs / f"run_{spec.run_index:03d}.last.txt"
            try:
                turn.final_text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        if exit_code not in (0, None) and not turn.result_seen:
            turn.is_error = True
        blob = " ".join(turn.errors) + " " + (stderr or "")[-3000:]
        if not turn.rate_limited and limits.LIMIT.search(blob):
            turn.rate_limited = True
        if turn.rate_limited and not turn.reset_at:
            turn.reset_at = limits.parse_reset(blob)
