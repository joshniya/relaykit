"""Interactive confirmation. relaykit never picks a model or reasoning effort for you silently:
every agent a relay may use — including failover agents — has its model, effort and options
confirmed once and saved (``confirmed = true``), and every ``relaykit run`` shows the full setup
and asks before it starts (``--yes`` skips only that last question)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from . import config, paths
from .adapters import get as get_adapter
from .config import AgentConfig, Settings


def interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return val or default


def yes(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    try:
        val = input(f"{prompt} [{d}]: ").strip().lower()
    except EOFError:
        return default
    if not val:
        return default
    return val in ("y", "yes")


def _model_choices(agent: AgentConfig) -> list:
    ad = get_adapter(agent.kind)
    models = []
    if hasattr(ad, "models"):
        try:
            models = [m["id"] for m in ad.models() if m.get("id")]
        except Exception:
            models = []
    return models or list(ad.models_hint)


def confirm_agent(root: Path, agent: AgentConfig, scope_path: Path) -> AgentConfig:
    """Ask for model / effort / extra args / limits for one agent and save them as confirmed."""
    ad = get_adapter(agent.kind)
    exe = ad.executable(agent)
    print()
    print(f"── {agent.name}: {ad.display} {'(beta: not live-tested)' if not ad.caps.tested else ''}")
    print(f"   CLI: {exe or 'NOT FOUND — install: ' + ad.install_hint}")
    if exe:
        v = ad.version(agent)
        if v:
            print(f"   version: {v}")
    cur_model, cur_effort = agent.model, agent.effort
    if not cur_model and hasattr(ad, "configured_default"):
        cm, ce = ad.configured_default()
        cur_model, cur_effort = cur_model or cm, cur_effort or ce
    choices = _model_choices(agent)
    if choices:
        print("   models: " + ", ".join(choices[:12]) + (" ..." if len(choices) > 12 else ""))
    model = ask("   Model (blank = the CLI's default)", cur_model)
    if ad.efforts:
        efforts = list(ad.efforts)
        if hasattr(ad, "models"):
            for m in ad.models():
                if m.get("id") == model and m.get("efforts"):
                    efforts = m["efforts"]
        print("   reasoning effort: " + ", ".join(efforts))
        effort = ask("   Reasoning / thinking effort (blank = the CLI's default)", cur_effort)
    else:
        print("   (this CLI has no reasoning-effort flag; set it in the CLI's own config if it supports one)")
        effort = ""
    extra = ask("   Extra CLI arguments (space-separated, optional)", " ".join(agent.extra_args or []))
    agent.model, agent.effort = model, effort
    agent.extra_args = extra.split() if extra else []
    hp = ask("   Hand off at % of context (blank = relay default)", f"{agent.handoff_pct:g}" if agent.handoff_pct else "")
    agent.handoff_pct = float(hp) if hp else 0
    cap = ask("   ...or at most this many tokens (blank = no cap; useful for 1M-token windows)",
              str(agent.handoff_tokens_max) if agent.handoff_tokens_max else "")
    agent.handoff_tokens_max = int(cap) if cap else 0
    if agent.kind == "claude" and exe and yes("   Measure this model's context window now? (one tiny request)", True):
        w = ad.probe_window(agent)
        if w:
            agent.context_window = w
            print(f"   context window: {w:,} tokens")
        else:
            print("   couldn't measure it; it'll be learned after the first run")
    agent.confirmed = True
    config.set_agent(scope_path, agent)
    print(f"   saved to {scope_path}")
    return agent


def ensure_confirmed(root: Path, s: Settings, *, accept_defaults: bool = False) -> Optional[str]:
    """Make sure every agent in the chain is confirmed. Returns an error message when it can't."""
    scope = paths.rk_dir(root) / paths.CONFIG_TOML
    missing = [n for n in s.chain() if not s.agent(n).confirmed]
    if not missing:
        return None
    if accept_defaults:
        for n in missing:
            s.agent(n).confirmed = True
        return None
    if not interactive():
        return ("these agents haven't had their model / reasoning effort confirmed: " + ", ".join(missing) +
                ". Run `relaykit agents` in a terminal (or pass --model/--effort, or --accept-defaults to use each "
                "CLI's own defaults).")
    print("Before the first run, confirm which model and reasoning effort each agent should use.")
    for n in missing:
        confirm_agent(root, s.agent(n), scope)
    return None


def summary(s: Settings, relay_name: str, workdir: Path) -> str:
    R = s.raw["relay"]
    lines = [f"relay:        {relay_name}", f"working dir:  {workdir}"]
    for i, n in enumerate(s.chain()):
        a = s.agent(n)
        ad = get_adapter(a.kind)
        tag = "start" if i == 0 else f"failover {i}"
        lines.append(f"agent ({tag}): {a.describe()}  [{ad.display}{'' if ad.caps.tested else ', beta'}]")
    caps = []
    if R.get("handoff_tokens_max"):
        caps.append(f"≤{int(R['handoff_tokens_max']):,} tokens")
    lines.append(f"context:      hand off at {R['handoff_pct']}%{' (' + ', '.join(caps) + ')' if caps else ''}, hard stop at {R['hard_pct']}%")
    budget = [f"{k.replace('max_', '')}={R[k]}" for k in ("max_hours", "max_sessions", "max_cost_usd", "max_runs") if R.get(k)]
    lines.append(f"budget:       {', '.join(budget) if budget else 'none'}")
    lines.append(f"git:          {s.git.get('mode', 'off')}")
    ch = [c for c in ("desktop", "ntfy_topic", "webhook_url") if s.notify.get(c)]
    lines.append(f"notify:       {', '.join(ch) if ch else 'off'}")
    lines.append(f"limits:       wait for the reset time the CLI reports; else {R['limit_wait_minutes']} min"
                 + (" (or fail over to the next agent)" if s.failover.get("enabled") and len(s.chain()) > 1 else ""))
    return "\n".join(lines)
