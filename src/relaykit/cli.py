"""relaykit command line."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, config, discover, gitops, installer, paths, procs, protocol, relays, wizard
from .adapters import REGISTRY, get as get_adapter
from .state import State

EPILOG = """\
typical flow:
  relaykit init                      profile this codebase, confirm agents, write .relaykit/
  relaykit plan my-feature --goal "..."   plan it WITH an agent (it asks you questions)
  relaykit run my-feature            run the relay (add --detach to run in the background)
  relaykit status / watch / stop my-feature
docs: https://github.com/joshniya/relaykit
"""


def _utf8_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _root(args) -> Path:
    return paths.find_project_root(getattr(args, "dir", None))


def _overrides(args) -> dict:
    o: dict = {"relay": {}, "agents": {}, "git": {}, "failover": {}}
    if getattr(args, "agent", None):
        o["relay"]["agent"] = args.agent
    for k in ("max_hours", "max_sessions", "max_cost_usd", "handoff_pct", "hard_pct"):
        v = getattr(args, k, None)
        if v is not None:
            o["relay"][k] = v
    if getattr(args, "no_keep_awake", False):
        o["relay"]["keep_awake"] = False
    if getattr(args, "git", None):
        o["git"]["mode"] = args.git
    if getattr(args, "failover", None):
        o["failover"] = {"enabled": True, "chain": [x.strip() for x in args.failover.split(",") if x.strip()]}
    target = getattr(args, "agent", None)
    model, effort = getattr(args, "model", None), getattr(args, "effort", None)
    if (model is not None or effort is not None):
        name = target or "__start__"
        ent = {}
        if model is not None:
            ent["model"] = model
        if effort is not None:
            ent["effort"] = effort
        ent["confirmed"] = True   # given explicitly on the command line
        o["agents"][name] = ent
    return o


def _settings(root: Path, relay_dir, args) -> config.Settings:
    o = _overrides(args)
    if "__start__" in o["agents"]:
        base = config.load(root, relay_dir)
        o["agents"][base.raw["relay"]["agent"]] = o["agents"].pop("__start__")
    return config.load(root, relay_dir, o)


# ----------------------------------------------------------------------------- commands
def cmd_version(args) -> int:
    print(f"relaykit {__version__} (python {sys.version.split()[0]})")
    return 0


def cmd_adapters(args) -> int:
    print(f"{'agent':10} {'name':22} {'installed':10} {'hooks':6} {'live usage':11} {'resume':7} status")
    for k, ad in REGISTRY.items():
        if k == "custom":
            continue
        a = config.AgentConfig(name=k)
        exe = ad.executable(a)
        print(f"{k:10} {ad.display:22} {'yes' if exe else 'no':10} {'yes' if ad.caps.hooks else '-':6} "
              f"{'yes' if ad.caps.live_usage else '-':11} {'yes' if ad.caps.resume else '-':7} "
              f"{'tested' if ad.caps.tested else 'beta'}")
    print("\ncustom     any other CLI, described in config (see docs/ADAPTERS.md)")
    return 0


def cmd_doctor(args) -> int:
    root = _root(args)
    ok = True
    print(f"relaykit {__version__} · python {sys.version.split()[0]} · {sys.platform}")
    if sys.version_info < (3, 9):
        print("  ✗ Python 3.9+ is required")
        ok = False
    print(f"project root: {root}")
    print(f"  .relaykit/: {'yes' if paths.rk_dir(root).is_dir() else 'no (run `relaykit init`)'}")
    print(f"  git: {'yes' if gitops.is_repo(root) else 'not a git repo (git checkpoints unavailable)'}")
    print("agents:")
    s = config.load(root)
    any_agent = False
    for k, ad in REGISTRY.items():
        if k == "custom":
            continue
        a = s.agents.get(k) or config.AgentConfig(name=k)
        exe = ad.executable(a)
        if not exe:
            continue
        any_agent = True
        v = ad.version(a)
        conf = "confirmed" if a.confirmed else "not confirmed yet"
        print(f"  ✓ {k:9} {v[:40]:40} {conf}{'' if ad.caps.tested else '  (beta)'}")
    if not any_agent:
        print("  ✗ no supported agent CLI found on PATH — install one (relaykit adapters lists them)")
        ok = False
    chain = s.chain()
    print(f"relay agent chain: {' > '.join(chain)}")
    for n in chain:
        a = s.agent(n)
        try:
            ad = get_adapter(a.kind)
        except KeyError as e:
            print(f"  ✗ {e}")
            ok = False
            continue
        if not ad.executable(a):
            print(f"  ✗ {n}: CLI not found ({ad.install_hint})")
            ok = False
    from .keepawake import KeepAwake
    with KeepAwake(True) as ka:
        print(f"keep-awake: {ka.how}")
    return 0 if ok else 1


def cmd_scan(args) -> int:
    root = _root(args)
    info = discover.scan(root)
    d = paths.rk_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / paths.PROJECT_JSON).write_text(json.dumps(info, indent=2), encoding="utf-8")
    md = d / paths.PROJECT_MD
    if md.exists() and not args.refresh:
        print(f"wrote {d / paths.PROJECT_JSON}; kept the existing {md} (use --refresh to regenerate; text after "
              f"'<!-- relaykit:keep -->' survives a refresh)")
    else:
        prev = md.read_text(encoding="utf-8") if md.exists() else None
        md.write_text(discover.render_profile(info, prev), encoding="utf-8")
        print(f"wrote {md}")
    langs = ", ".join(n for n, _ in info["languages"][:5]) or "?"
    print(f"{info['files_scanned']:,} files · {langs} · {len(info['commands'])} commands detected · "
          f"{len(info['instruction_files'])} instruction file(s)")
    for c in info["commands"][:12]:
        print(f"  {c['purpose']:12} {c['command']}")
    return 0


def cmd_init(args) -> int:
    root = _root(args)
    d = paths.rk_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    args.refresh = False
    cmd_scan(args)
    if installer.ensure_gitignore(root):
        print("added .relaykit/logs/ to .gitignore")
    cfg_path = d / paths.CONFIG_TOML
    data = config.read_layer(cfg_path)
    installed = [k for k, ad in REGISTRY.items() if k != "custom" and ad.executable(config.AgentConfig(name=k))]
    if not installed:
        print("No supported agent CLI found on PATH. Install one (see `relaykit adapters`), then run init again.")
        return 1
    start = args.agent or data.get("relay", {}).get("agent") or ("claude" if "claude" in installed else installed[0])
    if wizard.interactive() and not args.yes:
        print(f"\nAgents found: {', '.join(installed)}")
        start = wizard.ask("Which agent should start relays", start)
        chain = data.get("failover", {}).get("chain") or []
        others = [a for a in installed if a != start]
        if others and wizard.yes(f"Fail over to another agent while {start} is rate-limited or signed out?", bool(chain)):
            chain_txt = wizard.ask("Failover order (comma-separated)", ",".join(chain[1:] if chain[:1] == [start] else chain) or ",".join(others))
            data["failover"] = {"enabled": True, "chain": [start] + [x.strip() for x in chain_txt.split(",") if x.strip() and x.strip() != start]}
        else:
            data["failover"] = {"enabled": False, "chain": []}
        data.setdefault("relay", {})["agent"] = start
        config.write_layer(cfg_path, data, comment="relaykit settings (see docs/CONFIG.md)")
        s = config.load(root)
        for n in s.chain():
            a = s.agent(n)
            if not a.confirmed or wizard.yes(f"Re-confirm {n}'s model/effort?", False):
                wizard.confirm_agent(root, a, cfg_path)
        data = config.read_layer(cfg_path)
        if wizard.yes("\nGit checkpoints: run relays in their own git worktree and commit at each handoff?", gitops.is_repo(root)):
            data["git"] = {"mode": "worktree"}
        else:
            data["git"] = {"mode": "off"}
        if wizard.yes("Notifications (desktop / phone via ntfy / Discord-Slack webhook)?", False):
            n = {"desktop": wizard.yes("  desktop notifications?", True)}
            topic = wizard.ask("  ntfy.sh topic (blank = none)", "")
            hook = wizard.ask("  webhook URL (Discord/Slack/any; blank = none)", "")
            if topic:
                n["ntfy_topic"] = topic
            if hook:
                n["webhook_url"] = hook
            data["notify"] = n
        if wizard.yes("Budget caps (stop after N hours / sessions / $)?", False):
            r = data.setdefault("relay", {})
            for key, label in (("max_hours", "hours"), ("max_sessions", "sessions"), ("max_cost_usd", "USD (cost-equivalent where the CLI reports it)")):
                v = wizard.ask(f"  max {label} (0 = no cap)", str(r.get(key, 0)))
                r[key] = float(v) if key != "max_sessions" else int(float(v))
        config.write_layer(cfg_path, data, comment="relaykit settings (see docs/CONFIG.md)")
    else:
        data.setdefault("relay", {})["agent"] = start
        config.write_layer(cfg_path, data, comment="relaykit settings (see docs/CONFIG.md)")
    print(f"\nwrote {cfg_path}")
    if wizard.interactive() and not args.yes and wizard.yes("Install relaykit's skills for your agents in this project?", True):
        targets = ["agents", "claude"] + [t for t in ("kiro", "qwen") if t in installed]
        for p in installer.install_skills(targets, "project", root):
            print(f"  installed {p}")
    if args.deep:
        return _deep_init(root, args)
    print("\nNext: `relaykit init --deep` to have an agent verify the build/test commands and record the baseline,"
          "\n      then `relaykit plan <name> --goal \"...\"` (or `relaykit new <name> --plan PLAN.md`).")
    return 0


def _deep_init(root: Path, args) -> int:
    skill = installer.SKILLS / "relaykit-init" / "SKILL.md"
    plan = paths.rk_dir(root) / "init-plan.md"
    plan.write_text(
        "# Deep init: verify this project's profile\n\n## Goal\n"
        f"Follow the relaykit-init procedure in `{skill.as_posix()}` to verify and complete `.relaykit/PROJECT.md`.\n\n"
        "## Progress at a glance\n- [ ] **Phase 1 — Verify commands and record the test baseline**\n"
        "- [ ] **Phase 2 — Architecture notes, conventions and hazards**\n\n"
        "## Constraints\n- Change NO product code. Only `.relaykit/PROJECT.md` and the relay files may be edited.\n",
        encoding="utf-8")
    d = relays.create(root, "_init", title="Deep init", plan_src=plan, phase_zero=False, final_review=False, force=True)
    args.relay = str(d)
    args.detach = False
    return cmd_run(args)


def cmd_agents(args) -> int:
    root = _root(args)
    s = config.load(root)
    scope = (paths.user_config_dir() if args.scope == "user" else paths.rk_dir(root)) / paths.CONFIG_TOML
    names = args.names or s.chain()
    if args.model is not None or args.effort is not None or args.extra is not None or args.confirm:
        # non-interactive: set what was given, mark confirmed (the caller confirmed it with the user)
        for n in names:
            a = s.agent(n)
            if args.model is not None:
                a.model = args.model
            if args.effort is not None:
                a.effort = args.effort
            if args.extra is not None:
                a.extra_args = args.extra.split()
            if args.handoff_tokens_max is not None:
                a.handoff_tokens_max = args.handoff_tokens_max
            a.confirmed = True
            config.set_agent(scope, a)
            print(f"saved {a.describe()} (confirmed) to {scope}")
        return 0
    if not wizard.interactive():
        for n in names:
            a = s.agent(n)
            print(a.describe() + ("  (confirmed)" if a.confirmed else "  (NOT confirmed)"))
        return 0
    for n in names:
        wizard.confirm_agent(root, s.agent(n), scope)
    return 0


def cmd_new(args) -> int:
    root = _root(args)
    s = config.load(root)
    R = s.raw["relay"]
    if not (paths.rk_dir(root) / paths.PROJECT_MD).exists():
        args.refresh = False
        cmd_scan(args)
    d = relays.create(root, args.name, title=args.title or "", goal=args.goal or "",
                      plan_src=Path(args.plan) if args.plan else None,
                      phase_zero=R["phase_zero"] and not args.no_phase_zero,
                      final_review=R["final_review"] and not args.no_final_review, force=args.force,
                      handoff_pct=float(R["handoff_pct"]), hard_pct=float(R["hard_pct"]),
                      early_handoff_pct=float(R["early_handoff_pct"]))
    print(f"created {d}")
    for f in (paths.PLAN_FILE, paths.PROMPT_FILE, paths.PROGRESS_FILE):
        print(f"  {paths.rel(d / f, root)}")
    if not args.plan:
        print("\nPLAN.md is a skeleton. Fill it in (or run `relaykit plan` to do it with an agent) before running.")
    print(f"\nRun it: relaykit run {d.name}")
    return 0


def _interactive_cmd(agent: config.AgentConfig, prompt: str) -> list:
    ad = get_adapter(agent.kind)
    pre = ad.command_prefix(agent)
    if not pre:
        return []
    k, m, e = agent.kind, agent.model, agent.effort
    if k == "claude":
        return pre + (["--model", m] if m else []) + (["--effort", e] if e else []) + [prompt]
    if k == "codex":
        return pre + (["-m", m] if m else []) + (["-c", f'model_reasoning_effort="{e}"'] if e else []) + [prompt]
    if k in ("gemini", "qwen", "copilot"):
        return pre + (["-m" if k == "gemini" else "--model", m] if m else []) + ["-i", prompt]
    if k == "kiro":
        return pre + ["chat"] + (["--model", m] if m else []) + [prompt]
    if k == "opencode":
        return pre + ["--prompt", prompt]
    return []


def cmd_plan(args) -> int:
    root = _root(args)
    s = _settings(root, None, args)
    name = relays.slugify(args.name)
    d = paths.relays_dir(root) / name
    if not (paths.rk_dir(root) / paths.PROJECT_MD).exists():
        args.refresh = False
        cmd_scan(args)
    skill = (installer.SKILLS / "relaykit-plan" / "SKILL.md").as_posix()
    prompt = (f"Use the relaykit-plan skill to plan a relay named '{name}' in this project"
              + (f". Goal: {args.goal}" if args.goal else "")
              + f". If that skill isn't loaded, read {skill} and follow it exactly. Interview me before deciding anything "
                f"that is mine to decide.")
    agent = s.agent(args.agent or s.raw["relay"]["agent"])
    cmd = _interactive_cmd(agent, prompt)
    if not cmd:
        print("Open your agent in this folder and paste this:\n\n" + prompt)
        return 0
    print(f"Starting {agent.name} interactively to plan '{name}'. It will ask you questions, write "
          f"{paths.rel(d / paths.PLAN_FILE, root)} and create the relay folder.\n")
    env = procs.clean_env()
    return subprocess.call(cmd, cwd=str(root), env=env)


def _resolve(root: Path, relay: str) -> Path:
    d = paths.resolve_relay_dir(root, relay)
    if not (d / paths.PROGRESS_FILE).exists():
        raise SystemExit(f"no relay at {d} (expected {paths.PROGRESS_FILE}). `relaykit list` shows the relays here.")
    return d


def cmd_run(args) -> int:
    root = _root(args)
    d = _resolve(root, args.relay)
    accept = bool(getattr(args, "accept_defaults", False))
    s = _settings(root, d, args)
    err = wizard.ensure_confirmed(root, s, accept_defaults=accept)
    if err:
        print("relaykit: " + err, file=sys.stderr)
        return 1
    if not accept:
        s = _settings(root, d, args)   # pick up what was just confirmed and saved
    logs = paths.logs_dir(root, d)
    st = State(logs)
    if getattr(args, "reset_budget", False):
        st.data["totals"] = {"runs": 0, "sessions": 0, "cost_usd": 0.0, "seconds": 0.0}
        st.data["relay_started_at"] = time.time()
        st.save()
    running = st.get("pid")
    if (running and int(running) != os.getpid() and procs.pid_alive(int(running))
            and st.get("status") in ("running", "waiting", "starting")):
        print(f"relay '{d.name}' already has a supervisor running (pid {running}). `relaykit stop {d.name}` first.")
        return 1
    if getattr(args, "show", False):
        print(wizard.summary(s, d.name, root))
        return 0
    if not getattr(args, "yes", False):
        print(wizard.summary(s, d.name, root))
        if wizard.interactive() and not wizard.yes("\nStart the relay?", True):
            return 2
    if getattr(args, "detach", False):
        argv = [procs.python_exe(), "-m", "relaykit", "_guard", str(d), "--dir", str(root)] + _passthrough(args)
        env = procs.clean_env({"PYTHONPATH": str(Path(__file__).resolve().parent.parent)})
        pid = procs.detach(argv, root, env, logs / "guard.out.txt", new_window=bool(getattr(args, "window", False)))
        print(f"relay '{d.name}' started in the background (guard pid {pid}).")
        print(f"  watch:  relaykit watch {d.name}\n  status: relaykit status {d.name}\n  stop:   relaykit stop {d.name}")
        return 0
    from .supervisor import Supervisor
    return Supervisor(root, d, s, echo=not getattr(args, "quiet", False)).run()


def _passthrough(args) -> list:
    out = ["--yes"]
    for flag, attr in (("--agent", "agent"), ("--model", "model"), ("--effort", "effort"), ("--failover", "failover"),
                       ("--git", "git"), ("--max-hours", "max_hours"), ("--max-sessions", "max_sessions"),
                       ("--max-cost-usd", "max_cost_usd"), ("--handoff-pct", "handoff_pct"), ("--hard-pct", "hard_pct")):
        v = getattr(args, attr, None)
        if v is not None:
            out += [flag, str(v)]
    if getattr(args, "accept_defaults", False):
        out.append("--accept-defaults")
    if getattr(args, "no_keep_awake", False):
        out.append("--no-keep-awake")
    return out


def cmd_guard(args) -> int:
    """Keeps a detached supervisor alive: restarts it if it crashes before the relay is finished."""
    root = _root(args)
    d = _resolve(root, args.relay)
    logs = paths.logs_dir(root, d)
    st = State(logs)
    st.set(guard_pid=os.getpid())
    restarts = 0
    while True:
        argv = [procs.python_exe(), "-m", "relaykit", "run", str(d), "--dir", str(root), "--quiet"] + _passthrough(args)
        code = subprocess.call(argv, cwd=str(root), env=procs.clean_env({"PYTHONPATH": os.environ.get("PYTHONPATH", "")}))
        if code in (0, 2, 3, 4) or st.is_done() or State(logs).stop_requested():
            return code
        restarts += 1
        if restarts > 20:
            return code
        time.sleep(30)


def _fmt_ts(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts))) if ts else "-"


def _status_one(root: Path, d: Path, as_json: bool = False) -> dict:
    logs = paths.rk_dir(root) / "logs" / d.name
    st = State(logs) if logs.exists() else None
    data = st.data if st else {}
    prog = Path(data.get("progress") or d / paths.PROGRESS_FILE)
    summ = protocol.summary(prog if prog.exists() else d / paths.PROGRESS_FILE)
    pid = data.get("pid")
    alive = bool(pid) and procs.pid_alive(int(pid))
    final = data.get("status") in ("done", "budget", "stalled", "stopped", "blocked", "error")
    sup = data.get("status") if (alive or final) else ("not running" if data else "never run")
    out = {"relay": d.name, "progress": str(prog), "supervisor": sup, "pid": pid if alive else None, **summ,
           "current": data.get("current"), "totals": data.get("totals"), "waiting_until": data.get("waiting_until"),
           "waiting_why": data.get("waiting_why"), "limited_until": data.get("limited_until"),
           "worktree": data.get("worktree")}
    if not as_json:
        print(f"● {d.name}: {summ['status'] or '?'} — supervisor {out['supervisor']}" + (f" (pid {pid})" if alive else ""))
        print(f"  phase: {summ['phase']}\n  step:  {summ['step'][:160]}\n  session {summ['session']} · updated {summ['last_updated']}")
        cur = data.get("current") or {}
        if alive and cur:
            print(f"  now:   run {cur.get('run')} on {cur.get('agent')} ({cur.get('mode')}), context "
                  f"{cur.get('context_pct', '?')}% · started {_fmt_ts(cur.get('started'))}")
        if data.get("waiting_until"):
            print(f"  waiting until {_fmt_ts(data['waiting_until'])}: {data.get('waiting_why')}")
        lim = {k: v for k, v in (data.get("limited_until") or {}).items() if float(v) > time.time()}
        if lim:
            print("  rate-limited: " + ", ".join(f"{k} until {_fmt_ts(v)}" for k, v in lim.items()))
        t = data.get("totals") or {}
        if t:
            print(f"  totals: {t.get('runs', 0)} runs · {t.get('sessions', 0)} sessions · "
                  f"{float(t.get('seconds', 0)) / 3600:.1f} h · ${float(t.get('cost_usd', 0)):.2f} cost-equivalent")
        if data.get("worktree"):
            print(f"  worktree: {data['worktree']} (branch {data.get('branch')})")
    return out


def cmd_status(args) -> int:
    root = _root(args)
    ds = [_resolve(root, args.relay)] if args.relay else paths.list_relays(root)
    if not ds:
        print("no relays in this project (relaykit new / relaykit plan)")
        return 0
    res = [_status_one(root, d, args.json) for d in ds]
    if args.json:
        print(json.dumps(res if not args.relay else res[0], indent=2, default=str))
    return 0


def cmd_list(args) -> int:
    root = _root(args)
    for d in paths.list_relays(root):
        summ = protocol.summary(d / paths.PROGRESS_FILE)
        print(f"{d.name:30} {summ['status']:14} {summ['phase'][:60]}")
    return 0


def cmd_stop(args) -> int:
    root = _root(args)
    d = _resolve(root, args.relay)
    st = State(paths.logs_dir(root, d))
    st.request_stop(now=args.now)
    print(f"stop requested for '{d.name}'" + (" (ending the current run now)" if args.now else
                                              " (after the current run; add --now to end it immediately)"))
    return 0


def cmd_logs(args) -> int:
    root = _root(args)
    d = _resolve(root, args.relay)
    live = paths.logs_dir(root, d) / "live.log"
    if not live.exists():
        print("no logs yet")
        return 0
    lines = live.read_text(encoding="utf-8", errors="replace").splitlines()
    print("\n".join(lines[-args.n:]))
    return 0


def cmd_watch(args) -> int:
    root = _root(args)
    d = _resolve(root, args.relay)
    logs = paths.logs_dir(root, d)
    live = logs / "live.log"
    _status_one(root, d)
    print("─" * 80)
    pos = 0
    if live.exists():
        data = live.read_bytes()
        tail = data.decode("utf-8", errors="replace").splitlines()[-args.n:]
        print("\n".join(tail))
        pos = len(data)
    try:
        while True:
            time.sleep(1.0)
            if not live.exists():
                continue
            size = live.stat().st_size
            if size > pos:
                with open(live, "rb") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                pos = size
                sys.stdout.write(chunk.decode("utf-8", errors="replace"))
                sys.stdout.flush()
            if State(logs).is_done():
                print("\nrelay finished.")
                return 0
    except KeyboardInterrupt:
        return 0


def cmd_install(args) -> int:
    root = _root(args)
    targets = ["agents", "claude", "kiro", "qwen"] if args.target == "all" else [t.strip() for t in args.target.split(",")]
    bad = [t for t in targets if t not in installer.SKILL_DIRS]
    if bad:
        print(f"unknown target(s): {', '.join(bad)} (choose from {', '.join(installer.SKILL_DIRS)} or all)")
        return 1
    for p in installer.install_skills(targets, args.scope, root):
        print(f"installed {p}")
    if args.gemini_hooks:
        print(f"gemini hooks added to {installer.install_gemini_hooks(root, args.scope)}")
    return 0


def cmd_notify_test(args) -> int:
    from .notify import Notifier
    root = _root(args)
    s = config.load(root)
    Notifier(s.notify, args.relay or "test", log=print).send("complete", "This is a relaykit test notification.", force=True)
    print("sent (check your desktop / phone / channel)")
    return 0


def cmd_hook(args) -> int:
    from .hooks import main as hook_main
    return hook_main(["hook", args.agent_name])


# ----------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="relaykit", description="Run any AI coding agent on projects too big for one "
                                "context window: unattended sessions with file-based handoffs.",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", help="project folder (default: the current one, walking up to .relaykit/ or .git)")
    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    def run_flags(sp):
        sp.add_argument("--agent", help="agent that starts the relay (claude, codex, gemini, kiro, ...)")
        sp.add_argument("--model", help="model for that agent (counts as confirmed)")
        sp.add_argument("--effort", help="reasoning effort for that agent (counts as confirmed)")
        sp.add_argument("--failover", help="comma-separated agents to switch to while one is rate-limited")
        sp.add_argument("--git", choices=["off", "commit", "worktree"], help="git checkpoints")
        sp.add_argument("--max-hours", dest="max_hours", type=float)
        sp.add_argument("--max-sessions", dest="max_sessions", type=int)
        sp.add_argument("--max-cost-usd", dest="max_cost_usd", type=float)
        sp.add_argument("--handoff-pct", dest="handoff_pct", type=float)
        sp.add_argument("--hard-pct", dest="hard_pct", type=float)
        sp.add_argument("--accept-defaults", action="store_true", help="use each CLI's own model/effort defaults without confirming")
        sp.add_argument("--no-keep-awake", action="store_true")
        sp.add_argument("--yes", "-y", action="store_true", help="don't ask before starting")
        sp.add_argument("--dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    sp = sub.add_parser("version")
    sp.set_defaults(fn=cmd_version)
    sp = sub.add_parser("adapters", help="supported agent CLIs and what each can do")
    sp.set_defaults(fn=cmd_adapters)
    sp = sub.add_parser("doctor", help="check Python, agents, git, keep-awake")
    sp.set_defaults(fn=cmd_doctor)
    sp = sub.add_parser("scan", help="profile the codebase into .relaykit/PROJECT.md")
    sp.add_argument("--refresh", action="store_true", help="regenerate PROJECT.md (keeps text after <!-- relaykit:keep -->)")
    sp.set_defaults(fn=cmd_scan)
    sp = sub.add_parser("init", help="scan + confirm agents + write .relaykit/config.toml")
    sp.add_argument("--agent")
    sp.add_argument("--deep", action="store_true", help="then run an agent to verify commands and record the baseline")
    sp.add_argument("--yes", "-y", action="store_true")
    sp.add_argument("--accept-defaults", action="store_true")
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(fn=cmd_init, model=None, effort=None, failover=None, git=None)
    sp = sub.add_parser("agents", help="confirm/edit each agent's model, reasoning effort and options")
    sp.add_argument("names", nargs="*")
    sp.add_argument("--scope", choices=["project", "user"], default="project")
    sp.add_argument("--model", help="set the model (non-interactive; marks the agent confirmed)")
    sp.add_argument("--effort", help="set the reasoning effort (non-interactive; marks the agent confirmed)")
    sp.add_argument("--extra", help="extra CLI arguments, space-separated (non-interactive)")
    sp.add_argument("--handoff-tokens-max", dest="handoff_tokens_max", type=int)
    sp.add_argument("--confirm", action="store_true", help="mark the agents' current settings as confirmed")
    sp.set_defaults(fn=cmd_agents)
    sp = sub.add_parser("new", help="create a relay folder from a plan")
    sp.add_argument("name")
    sp.add_argument("--plan", help="an existing plan file to use as PLAN.md")
    sp.add_argument("--goal")
    sp.add_argument("--title")
    sp.add_argument("--no-phase-zero", action="store_true")
    sp.add_argument("--no-final-review", action="store_true")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_new)
    sp = sub.add_parser("plan", help="plan a relay interactively with an agent (it interviews you)")
    sp.add_argument("name")
    sp.add_argument("--goal")
    sp.add_argument("--agent")
    sp.set_defaults(fn=cmd_plan, model=None, effort=None)
    for name, detach in (("run", False), ("start", True)):
        sp = sub.add_parser(name, help="run a relay" + (" in the background" if detach else " (foreground; --detach for background)"))
        sp.add_argument("relay", help="relay name (.relaykit/relays/<name>) or a folder with RELAY_PROMPT.md + RELAY_PROGRESS.md")
        run_flags(sp)
        sp.add_argument("--detach", action="store_true", default=detach)
        sp.add_argument("--window", action="store_true", help="(Windows) run the background supervisor in its own console window")
        sp.add_argument("--reset-budget", action="store_true")
        sp.add_argument("--quiet", action="store_true")
        sp.add_argument("--show", action="store_true", help="print the run setup and exit")
        sp.set_defaults(fn=cmd_run)
    sp = sub.add_parser("_guard")
    sp.add_argument("relay")
    run_flags(sp)
    sp.set_defaults(fn=cmd_guard)
    sp = sub.add_parser("status", help="where each relay is")
    sp.add_argument("relay", nargs="?")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_status)
    sp = sub.add_parser("list", help="relays in this project")
    sp.set_defaults(fn=cmd_list)
    sp = sub.add_parser("watch", help="follow a relay live")
    sp.add_argument("relay")
    sp.add_argument("-n", type=int, default=40)
    sp.set_defaults(fn=cmd_watch)
    sp = sub.add_parser("logs", help="print the end of a relay's log")
    sp.add_argument("relay")
    sp.add_argument("-n", type=int, default=80)
    sp.set_defaults(fn=cmd_logs)
    sp = sub.add_parser("stop", help="stop a relay after its current run (or --now)")
    sp.add_argument("relay")
    sp.add_argument("--now", action="store_true")
    sp.set_defaults(fn=cmd_stop)
    sp = sub.add_parser("install", help="install relaykit's skills into agent folders")
    sp.add_argument("--for", dest="target", default="agents,claude", help="agents,claude,kiro,qwen or all")
    sp.add_argument("--scope", choices=["project", "user"], default="project")
    sp.add_argument("--gemini-hooks", action="store_true", help="also add relaykit's (relay-only) hooks to Gemini settings")
    sp.set_defaults(fn=cmd_install)
    sp = sub.add_parser("notify-test", help="send a test notification with the configured channels")
    sp.add_argument("relay", nargs="?")
    sp.set_defaults(fn=cmd_notify_test)
    sp = sub.add_parser("hook")
    sp.add_argument("agent_name")
    sp.set_defaults(fn=cmd_hook)
    # internal subcommands stay callable but out of the help listing
    sub._choices_actions = [a for a in sub._choices_actions if a.dest not in ("_guard", "hook")]
    return p


def main(argv=None) -> int:
    _utf8_console()
    p = build_parser()
    args = p.parse_args(argv)
    if not getattr(args, "fn", None):
        p.print_help()
        return 0
    try:
        return int(args.fn(args) or 0)
    except KeyboardInterrupt:
        return 130
