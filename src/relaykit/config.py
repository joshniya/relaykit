"""Settings, layered (later wins):

    built-in defaults  <  ~/.relaykit/config.toml  <  <project>/.relaykit/config.toml
                       <  <relay>/relay.toml        <  command-line flags

Example ``.relaykit/config.toml``::

    [relay]
    agent = "claude"            # which agent starts the relay
    handoff_pct = 35            # ask for a handoff at this % of the context window
    hard_pct = 45               # refuse everything but the handoff from here
    max_hours = 0               # budget caps; 0 = unlimited
    max_sessions = 0
    max_cost_usd = 0

    [agents.claude]
    model = "claude-opus-5"
    effort = "high"
    confirmed = true            # you confirmed model + effort; relaykit asks until you do

    [agents.codex]
    model = "gpt-5.5-codex"
    effort = "high"
    confirmed = true

    [failover]
    enabled = true
    chain = ["claude", "codex"] # used in order while an agent is rate-limited or signed out

    [git]
    mode = "worktree"           # off | commit | worktree

    [notify]
    desktop = true
    ntfy_topic = "my-relays"
    webhook_url = ""
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import paths, tomlio

DEFAULTS: dict = {
    "relay": {
        "agent": "claude",
        "handoff_pct": 35.0,
        "hard_pct": 45.0,
        "handoff_tokens_max": 0,        # absolute caps (0 = none); per agent too
        "hard_tokens_max": 0,
        "early_handoff_pct": 20.0,      # hand off after finishing a phase when past this
        "max_stalled_sessions": 3,      # sessions in a row that hand off without touching the progress file
        "max_blocked": 4,               # RELAY BLOCKED this many times in a row -> stop and notify
        "enforcement": "auto",          # auto | hooks | checkpoints
        "kill_at_hard": True,           # agents that report usage live but have no hooks: stop the run at hard_pct
        "max_runs": 400,
        "max_sessions": 0,
        "max_hours": 0.0,
        "max_cost_usd": 0.0,
        "max_resumes_per_session": 6,
        "max_checkpoints_per_session": 60,
        "blind_checkpoints_per_session": 6,  # agents that report no usage at all: hand off after this many steps
        "hang_minutes": 45,
        "retry_base_seconds": 90,
        "retry_max_seconds": 900,
        "blocked_wait_minutes": 15,
        "auth_wait_minutes": 15,
        "max_auth_waits": 32,
        "limit_wait_minutes": 20,       # usage-limit wait when the CLI gives no reset time
        "limit_margin_seconds": 90,     # added to a known reset time
        "gap_seconds": 8,
        "keep_awake": True,
        "phase_zero": True,             # new relays start with an orientation + baseline phase
        "final_review": True,           # new relays end with a review + final-report phase
    },
    "failover": {
        "enabled": False,
        "chain": [],
        "switch_back": True,
        "on_auth_error": True,
    },
    "git": {
        "mode": "off",                  # off | commit | worktree
        "branch": "relaykit/{relay}",
        "worktree_dir": "",             # default: <repo>/../<repo-name>.relaykit-<relay>
        "commit_on": ["handoff", "complete"],
    },
    "notify": {
        "events": ["complete", "blocked", "failed", "limit", "failover", "budget", "auth"],
        "desktop": False,
        "ntfy_topic": "",
        "ntfy_server": "https://ntfy.sh",
        "webhook_url": "",
    },
    "agents": {},
}


@dataclass
class AgentConfig:
    name: str
    adapter: str = ""
    model: str = ""
    effort: str = ""
    extra_args: list = field(default_factory=list)
    binary: str = ""
    context_window: int = 0
    confirmed: bool = False
    env: dict = field(default_factory=dict)
    # per-agent context limits (0 = use [relay] values). Absolute caps matter for 1M-token windows:
    # 35% of 1M is ~350k tokens, well past where most models' work quality starts to drop.
    handoff_pct: float = 0
    hard_pct: float = 0
    handoff_tokens_max: int = 0
    hard_tokens_max: int = 0
    # only for the "custom" adapter
    command: list = field(default_factory=list)
    resume_command: list = field(default_factory=list)
    prompt_via: str = ""             # "arg" | "stdin" (adapter default when empty)

    @property
    def kind(self) -> str:
        return self.adapter or self.name

    def describe(self) -> str:
        bits = [self.name]
        bits.append(f"model={self.model or '(CLI default)'}")
        bits.append(f"effort={self.effort or '(CLI default)'}")
        if self.extra_args:
            bits.append("args=" + " ".join(map(str, self.extra_args)))
        if self.handoff_pct or self.handoff_tokens_max:
            lim = f"{self.handoff_pct:g}%" if self.handoff_pct else ""
            if self.handoff_tokens_max:
                lim += ("/" if lim else "") + f"≤{self.handoff_tokens_max:,} tok"
            bits.append(f"handoff={lim}")
        if self.context_window:
            bits.append(f"window={self.context_window:,}")
        return "  ".join(bits)


@dataclass
class Settings:
    raw: dict
    agents: dict

    def __getattr__(self, item: str) -> Any:  # relay.* keys as attributes
        relay = self.__dict__.get("raw", {}).get("relay", {})
        if item in relay:
            return relay[item]
        raise AttributeError(item)

    @property
    def failover(self) -> dict:
        return self.raw["failover"]

    @property
    def git(self) -> dict:
        return self.raw["git"]

    @property
    def notify(self) -> dict:
        return self.raw["notify"]

    def agent(self, name: str) -> AgentConfig:
        if name not in self.agents:
            self.agents[name] = AgentConfig(name=name)
        return self.agents[name]

    def chain(self) -> list:
        """Agents in failover order, the starting agent first."""
        first = self.raw["relay"]["agent"]
        order = [first]
        if self.failover.get("enabled"):
            for a in self.failover.get("chain", []):
                if a not in order:
                    order.append(a)
        return order


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read(path: Path) -> dict:
    if path and Path(path).is_file():
        return tomlio.load_file(path)
    return {}


def layers(root: Path, relay_dir: Optional[Path] = None) -> list:
    out = [("user", paths.user_config_dir() / paths.CONFIG_TOML),
           ("project", paths.rk_dir(root) / paths.CONFIG_TOML)]
    if relay_dir is not None:
        out.append(("relay", Path(relay_dir) / paths.RELAY_TOML))
    return out


def load(root: Path, relay_dir: Optional[Path] = None, overrides: Optional[dict] = None) -> Settings:
    data = copy.deepcopy(DEFAULTS)
    for _, p in layers(root, relay_dir):
        data = deep_merge(data, _read(p))
    data = deep_merge(data, overrides or {})
    agents = {}
    for name, a in (data.get("agents") or {}).items():
        a = dict(a or {})
        known = {k: a[k] for k in AgentConfig.__dataclass_fields__ if k in a and k != "name"}
        agents[name] = AgentConfig(name=name, **known)
    return Settings(raw=data, agents=agents)


def read_layer(path: Path) -> dict:
    return _read(path)


def write_layer(path: Path, data: dict, comment: str = "") -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(tomlio.dumps(data, header_comment=comment), encoding="utf-8")


def set_agent(path: Path, agent: AgentConfig) -> None:
    """Write one agent's confirmed settings into a config layer, keeping everything else."""
    data = _read(path)
    entry = data.setdefault("agents", {}).setdefault(agent.name, {})
    if agent.adapter and agent.adapter != agent.name:
        entry["adapter"] = agent.adapter
    entry["model"] = agent.model
    entry["effort"] = agent.effort
    if agent.extra_args:
        entry["extra_args"] = list(agent.extra_args)
    elif "extra_args" in entry:
        del entry["extra_args"]
    if agent.context_window:
        entry["context_window"] = int(agent.context_window)
    if agent.binary:
        entry["binary"] = agent.binary
    for k in ("handoff_pct", "hard_pct", "handoff_tokens_max", "hard_tokens_max"):
        v = getattr(agent, k)
        if v:
            entry[k] = v
        else:
            entry.pop(k, None)
    entry["confirmed"] = bool(agent.confirmed)
    write_layer(path, data, comment="relaykit settings (see docs/CONFIG.md)")
