"""The adapter contract: everything relaykit needs to drive one agent CLI headlessly.

An adapter turns a RunSpec (new session / resume / forced handoff, with a prompt) into a command
line, then reads the CLI's output line by line into a Turn: the session id, how full the context
is, the final assistant text (where the marker lives), cost, rate-limit signals and errors.

Capabilities decide how the context limit is enforced for that agent:
  hooks       relaykit installs per-run hooks: a warning after each tool call past the handoff
              point, a refusal of every tool but the handoff past the hard point, and a stop gate
              that won't let a turn end without a marker. (Best: works mid-turn.)
  live_usage  usage is visible while the turn runs. Without hooks, the supervisor ends the run at
              the hard point and resumes the session with "hand off now".
  neither     checkpoint mode: the agent ends its turn after every step with RELAY CHECKPOINT and
              the supervisor answers "continue" or "hand off now" from the usage it last saw.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import procs
from ..config import AgentConfig


@dataclass
class Caps:
    hooks: bool = False
    live_usage: bool = False
    resume: bool = True
    preset_session_id: bool = False
    cost: bool = False
    structured_limits: bool = False
    tested: bool = False          # live-tested end to end (otherwise "beta")


@dataclass
class RunSpec:
    mode: str                     # "new" | "resume" | "handoff"
    prompt: str
    session_id: str               # resume target; for "new", a preset id when the CLI accepts one
    workdir: Path
    relay_dir: Path
    logs: Path
    run_index: int
    label: str
    agent: AgentConfig
    hooks: bool
    window: int


@dataclass
class Launch:
    argv: list
    stdin: Optional[str] = None
    env: dict = field(default_factory=dict)
    session_id: str = ""


@dataclass
class Turn:
    session_id: str = ""
    model: str = ""
    ctx_tokens: int = 0
    ctx_pct: Optional[float] = None          # when the CLI reports a percentage itself
    window: int = 0
    final_text: str = ""
    result_seen: bool = False
    is_error: bool = False
    errors: list = field(default_factory=list)
    cost_usd: float = 0.0
    tools: int = 0
    rate_limited: bool = False
    reset_at: Optional[float] = None
    limit_kind: str = ""
    extra: dict = field(default_factory=dict)

    def pct(self, window: int) -> float:
        if self.ctx_pct is not None:
            return float(self.ctx_pct)
        w = self.window or window
        return 100.0 * self.ctx_tokens / w if (w and self.ctx_tokens) else 0.0


_NPM_SHIM_JS = re.compile(r'"%dp0%\\([^"]+?\.(?:js|cjs|mjs))"', re.I)


def resolve_npm_shim(cmd_path: str) -> Optional[list]:
    """Turn an npm ``X.cmd`` shim into ``[node, script.js]`` so arguments reach the program intact
    (cmd.exe re-parses a batch file's arguments; node.exe does not)."""
    try:
        text = Path(cmd_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _NPM_SHIM_JS.search(text)
    if not m:
        return None
    base = Path(cmd_path).parent
    script = base / m.group(1)
    if not script.exists():
        return None
    node = base / "node.exe"
    node_exe = str(node) if node.exists() else (procs.which("node") or "node")
    return [node_exe, str(script)]


class Adapter:
    id = "base"
    display = "Base"
    binary = ""
    caps = Caps()
    efforts: tuple = ()
    models_hint: tuple = ()
    install_hint = ""
    login_hint = ""
    docs_url = ""

    # ---- discovery
    def executable(self, agent: AgentConfig) -> Optional[str]:
        return procs.which(agent.binary or self.binary)

    def command_prefix(self, agent: AgentConfig) -> Optional[list]:
        exe = self.executable(agent)
        if not exe:
            return None
        if procs.is_batch(exe):
            shim = resolve_npm_shim(exe)
            if shim:
                return shim
        return [exe]

    def version(self, agent: AgentConfig) -> str:
        pre = self.command_prefix(agent)
        if not pre:
            return ""
        try:
            r = subprocess.run(pre + ["--version"], capture_output=True, text=True, timeout=30,
                               encoding="utf-8", errors="replace")
            return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else ""
        except Exception:
            return ""

    def default_window(self, model: str) -> int:
        return 200_000

    # ---- one run
    def build(self, spec: RunSpec) -> Launch:
        raise NotImplementedError

    def on_line(self, line: str, turn: Turn) -> list:
        """Parse one stdout line into ``turn``. Return live-log lines: [(kind, text), ...] where
        kind is one of init/text/tool/info/error."""
        return []

    def poll(self, spec: RunSpec, turn: Turn) -> list:
        """Called every couple of seconds while the run is alive (side channels, e.g. a session log)."""
        return []

    def finish(self, spec: RunSpec, turn: Turn, exit_code: Optional[int], stderr: str) -> None:
        """Called once after the process exits."""

    # ---- helpers for subclasses
    @staticmethod
    def hint(inp: dict, workdir: Path) -> str:
        if not isinstance(inp, dict):
            return ""
        for k in ("file_path", "path", "notebook_path", "command", "cmd", "pattern", "query", "description", "prompt", "url"):
            v = inp.get(k)
            if v:
                s = v if isinstance(v, str) else " ".join(map(str, v)) if isinstance(v, list) else str(v)
                return s.replace(str(workdir), "").lstrip("\\/")
        return ""
