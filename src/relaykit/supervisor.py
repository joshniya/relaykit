"""The supervisor: runs agent sessions back to back until the relay is done.

One loop iteration = one agent process ("run"). A run is a fresh session, a resumed session
("continue"), or a forced handoff turn. After each run the supervisor reads the outcome — the
marker on the reply's last line, the progress file, usage, rate limits, errors — and decides what
happens next (see ``decide``). The progress file is the only real state, so the supervisor can be
killed and restarted at any point; it also lets a DIFFERENT agent pick up the work (failover).
"""
from __future__ import annotations

import json
import os
import queue
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import gitops, limits, paths, procs, protocol
from .adapters import Adapter, RunSpec, Turn, get as get_adapter
from .config import AgentConfig, Settings
from .keepawake import KeepAwake
from .notify import Notifier
from .state import State

EXIT_DONE, EXIT_ERROR, EXIT_STOPPED, EXIT_BUDGET, EXIT_BLOCKED = 0, 1, 2, 3, 4


@dataclass
class Outcome:
    marker: Optional[str] = None
    complete_status: bool = False
    progress_touched: bool = False
    rate_limited: bool = False
    reset_at: Optional[float] = None
    limit_kind: str = ""
    context_overflow: bool = False
    auth_error: bool = False
    limit_text: bool = False
    busy: bool = False
    failed: bool = False
    no_result: bool = False
    hung: bool = False
    killed_for_context: bool = False
    stopped_now: bool = False
    seconds: float = 0.0
    ctx_tokens: int = 0
    ctx_pct: float = 0.0
    usage_known: bool = False
    tail: str = ""


@dataclass
class Action:
    kind: str                      # done | new | resume | handoff | wait | failover | stop
    reason: str = ""
    wait_seconds: float = 0
    then: str = ""                 # for wait: new | resume
    exit_code: int = EXIT_STOPPED
    count_run: bool = True


@dataclass
class Ctx:
    """Mutable loop context (kept together so decide() stays a pure-ish function)."""
    mode: str = "new"
    mode0: str = "new"
    resumes: int = 0
    checkpoints: int = 0
    retry_wait: float = 90
    auth_waits: int = 0
    stalled: int = 0
    blocked_streak: int = 0
    handoff_tokens: int = 0
    window: int = 0
    failover_available: bool = False


def decide(o: Outcome, c: Ctx, s: Settings) -> Action:
    """What happens after a run. Pure: reads the outcome + loop context, returns an Action."""
    R = s.raw["relay"]
    if o.stopped_now:
        return Action("stop", "stop requested", exit_code=EXIT_STOPPED)
    if o.marker == protocol.COMPLETE:
        if o.complete_status:
            return Action("done", "RELAY COMPLETE printed and the progress file says so")
        return Action("resume", "RELAY COMPLETE printed but the progress file's Status isn't RELAY COMPLETE")
    if o.marker == protocol.HANDOFF:
        return Action("new", "handoff: progress saved")
    if o.marker == protocol.BLOCKED:
        if c.blocked_streak + 1 >= int(R["max_blocked"]):
            return Action("stop", f"RELAY BLOCKED {c.blocked_streak + 1} times in a row: the owner is needed",
                          exit_code=EXIT_BLOCKED)
        return Action("wait", "agent reported RELAY BLOCKED", wait_seconds=60 * float(R["blocked_wait_minutes"]),
                      then="new", count_run=False)
    if o.marker == protocol.CHECKPOINT:
        if c.handoff_tokens and o.ctx_tokens >= c.handoff_tokens:
            return Action("handoff", f"checkpoint at {o.ctx_pct:.0f}% context: past the handoff point")
        if c.checkpoints + 1 >= int(R["max_checkpoints_per_session"]):
            return Action("handoff", "checkpoint budget for this session used up")
        if not o.usage_known and c.checkpoints + 1 >= int(R["blind_checkpoints_per_session"]):
            return Action("handoff", "this agent reports no usage: handing off after a fixed number of steps")
        return Action("resume", f"checkpoint at {o.ctx_pct:.0f}% context: continue")
    if o.killed_for_context:
        return Action("handoff", f"stopped the run at the hard limit ({o.ctx_pct:.0f}% context)")
    if o.rate_limited or (o.limit_text and (o.failed or o.no_result)):
        if c.failover_available:
            return Action("failover", "usage limit reached")
        if o.reset_at and o.reset_at > time.time():
            secs = o.reset_at - time.time() + float(R["limit_margin_seconds"])
            return Action("wait", "usage limit reached; waiting for the reset the CLI reported",
                          wait_seconds=secs, then="resume", count_run=False)
        return Action("wait", "usage limit reached; no reset time given, waiting the fallback timer",
                      wait_seconds=60 * float(R["limit_wait_minutes"]), then="resume", count_run=False)
    if o.context_overflow:
        return Action("new", "context too long for the model: fresh session (state is in the progress file)")
    if o.auth_error and not (not o.failed and o.seconds > 60):
        if c.failover_available and s.failover.get("on_auth_error", True):
            return Action("failover", "sign-in / auth error")
        if c.auth_waits + 1 > int(R["max_auth_waits"]):
            return Action("stop", "still signed out after the maximum number of waits", exit_code=EXIT_ERROR)
        return Action("wait", "signed out or auth error: sign in again from a terminal",
                      wait_seconds=60 * float(R["auth_wait_minutes"]), then="resume", count_run=False)
    if o.hung or o.failed or o.no_result or o.busy:
        if o.no_result and o.seconds < 30 and c.mode0 != "new":
            return Action("new", "resume failed straight away: starting a fresh session instead")
        return Action("wait", f"run failed (hung={o.hung}, error={o.tail[-160:]!r}); backing off",
                      wait_seconds=c.retry_wait, then="resume")
    if c.mode0 == "handoff":
        return Action("new", "forced handoff turn ended without the marker: fresh session anyway")
    if c.resumes + 1 > int(R["max_resumes_per_session"]):
        return Action("handoff", "too many turns without a marker: asking for a handoff")
    return Action("resume", "turn ended without a marker: continuing the same session")


class Supervisor:
    def __init__(self, root: Path, relay_dir: Path, settings: Settings, *, echo: bool = True,
                 sleep: Callable[[float], None] = None):
        self.root = Path(root).resolve()
        self.relay_dir = Path(relay_dir).resolve()
        self.s = settings
        self.R = settings.raw["relay"]
        self.name = self.relay_dir.name
        self.logs = paths.logs_dir(self.root, self.relay_dir)
        self.state = State(self.logs)
        self.echo = echo
        self._sleep = sleep or time.sleep
        self.live_log = self.logs / "live.log"
        self.events = self.logs / "events.jsonl"
        self.notifier = Notifier(settings.notify, self.name, log=lambda m: self.log(m, "warn"))
        self.workdir = self.root
        self.progress = self.relay_dir / paths.PROGRESS_FILE
        self.prompt_file = self.relay_dir / paths.PROMPT_FILE
        self._final_turn: Optional[str] = None

    # ------------------------------------------------------------------ logging
    def log(self, msg: str, kind: str = "info") -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        with open(self.live_log, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if self.echo:
            self._print(line)

    def live(self, text: str) -> None:
        with open(self.live_log, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
        if self.echo:
            self._print(text)

    @staticmethod
    def _print(text: str) -> None:
        try:
            print(text, flush=True)
        except UnicodeEncodeError:
            print(text.encode("ascii", "replace").decode("ascii"), flush=True)

    def event(self, kind: str, **data) -> None:
        rec = {"ts": time.time(), "event": kind, **data}
        with open(self.events, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    def notify(self, event: str, message: str) -> None:
        self.event("notify", what=event, message=message)
        self.notifier.send(event, message)

    def sleep(self, seconds: float, why: str = "") -> bool:
        """Sleep in small slices so a stop request is honoured. Returns False if stopped."""
        end = time.time() + max(0.0, seconds)
        if seconds >= 60:
            self.state.set(status="waiting", waiting_until=end, waiting_why=why)
        while time.time() < end:
            if self.state.stop_requested():
                return False
            self._sleep(min(5.0, max(0.0, end - time.time())))
        if seconds >= 60:
            self.state.set(status="running", waiting_until=None, waiting_why="")
        return True

    # ------------------------------------------------------------------ agents
    def adapter_for(self, agent: AgentConfig) -> Adapter:
        return get_adapter(agent.kind)

    def window_for(self, agent: AgentConfig, adapter: Adapter) -> int:
        if agent.context_window:
            return int(agent.context_window)
        learned = (self.state.data.get("windows") or {}).get(f"{agent.name}:{agent.model}")
        if learned:
            return int(learned)
        cache = paths.user_config_dir() / "windows.json"
        try:
            w = json.loads(cache.read_text(encoding="utf-8")).get(f"{agent.kind}:{agent.model}")
            if w:
                return int(w)
        except Exception:
            pass
        return adapter.default_window(agent.model)

    def learn_window(self, agent: AgentConfig, window: int) -> None:
        if not window or agent.context_window:
            return
        key = f"{agent.name}:{agent.model}"
        if self.state.data["windows"].get(key) == window:
            return
        self.state.data["windows"][key] = window
        self.state.save()
        self.log(f"context window learned from {agent.name}: {window:,} tokens")
        cache = paths.user_config_dir() / "windows.json"
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            data = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
            data[f"{agent.kind}:{agent.model}"] = window
            cache.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def thresholds(self, agent: AgentConfig, window: int) -> tuple:
        hp = float(agent.handoff_pct or self.R["handoff_pct"])
        xp = float(agent.hard_pct or self.R["hard_pct"])
        handoff = int(window * hp / 100.0)
        hard = int(window * xp / 100.0)
        cap_h = int(agent.handoff_tokens_max or self.R.get("handoff_tokens_max") or 0)
        cap_x = int(agent.hard_tokens_max or self.R.get("hard_tokens_max") or 0)
        if cap_h:
            handoff = min(handoff, cap_h)
        if cap_x:
            hard = min(hard, cap_x)
        return handoff, max(hard, handoff + 1)

    def available(self, name: str) -> bool:
        agent = self.s.agent(name)
        try:
            ad = self.adapter_for(agent)
        except KeyError:
            return False
        if not ad.executable(agent):
            return False
        until = float(self.state.data["limited_until"].get(name) or 0)
        return until <= time.time()

    def next_agent(self, current: str) -> Optional[str]:
        chain = self.s.chain()
        for name in chain:
            if name != current and self.available(name):
                return name
        return None

    def preferred_agent(self) -> str:
        chain = self.s.chain()
        for name in chain:
            if self.available(name):
                return name
        return chain[0]

    # ------------------------------------------------------------------ prompts
    def _rel(self, p: Path) -> str:
        return paths.rel(p, self.workdir)

    def prompts(self, agent: AgentConfig, adapter: Adapter, checkpoint_mode: bool, ctx_pct: float,
                takeover_note: str = "") -> dict:
        pr, pg = self._rel(self.prompt_file), self._rel(self.progress)
        markers = "RELAY HANDOFF (after saving progress), RELAY COMPLETE (when the whole plan is finished)"
        if checkpoint_mode:
            markers += " or RELAY CHECKPOINT (a step is done and saved)"
        who = f"You are running as {adapter.display}" + (f" ({agent.model})" if agent.model else "") + "."
        cp = ("CHECKPOINT MODE: this agent can't be watched while a turn runs, so work in steps. After each step "
              "(a few files, or about 15-20 minutes of work), save the progress file and end your turn with RELAY "
              "CHECKPOINT on the last line. The supervisor will answer 'continue' or ask you to hand off. "
              if checkpoint_mode else "")
        start = (f"You are a session of an unattended relay run by relaykit. Read {pr} and follow it exactly. The "
                 f"current state is in {pg}. {who} {cp}{takeover_note}Nobody is watching: never ask questions, never "
                 f"wait for a reply.").replace("  ", " ")
        cont = (f"continue. Re-read {pg} and keep following {pr} from the next action. {cp}End your turn only with "
                f"{markers} on the last line.")
        if checkpoint_mode and ctx_pct:
            cont = f"Context is at {ctx_pct:.0f}%, below the handoff point. " + cont
        handoff = (f"Stop at a clean point now. Save {pg} exactly as {pr} describes for a handoff (Current, Next action, "
                   f"phase rows, files touched, decision log, a session-log line ending with 'handoff'), then reply with "
                   f"RELAY HANDOFF on the last line. Do not start new work.")
        return {"new": start, "resume": cont, "handoff": handoff}

    # ------------------------------------------------------------------ git
    def setup_git(self) -> None:
        mode = (self.s.git.get("mode") or "off").lower()
        if mode == "off":
            return
        if not gitops.is_repo(self.root):
            self.log("git checkpoints are on but this isn't a git repository: turning them off for this run", "warn")
            self.s.git["mode"] = "off"
            return
        if mode == "worktree":
            top = gitops.toplevel(self.root) or self.root
            try:
                rel_relay = self.relay_dir.relative_to(top).as_posix()
            except ValueError:
                raise RuntimeError("worktree mode needs the relay folder inside the repository")
            wt, br, created = gitops.ensure_worktree(top, self.name, self.s.git.get("branch") or "relaykit/{relay}",
                                                     self.s.git.get("worktree_dir") or "")
            if created:
                copied = gitops.copy_untracked_into(top, wt, [rel_relay, ".relaykit/PROJECT.md",
                                                              ".relaykit/config.toml", ".relaykit/project.json"])
                self.log(f"created worktree {wt} on branch {br}" + (f" (copied: {', '.join(copied)})" if copied else ""))
            self.workdir = Path(wt)
            self.progress = self.workdir / rel_relay / paths.PROGRESS_FILE
            self.prompt_file = self.workdir / rel_relay / paths.PROMPT_FILE
            self.state.set(worktree=str(wt), branch=br)

    def checkpoint_commit(self, why: str) -> None:
        mode = (self.s.git.get("mode") or "off").lower()
        if mode == "off" or why not in (self.s.git.get("commit_on") or []):
            return
        summ = protocol.summary(self.progress)
        msg = f"relaykit({self.name}): {why} after session {summ['session'] or '?'} — {summ['phase'][:80]}"
        ok, detail = gitops.commit_all(self.workdir, msg)
        self.log(f"git checkpoint ({why}): {detail}" if ok else f"git checkpoint FAILED ({why}): {detail}",
                 "info" if ok else "warn")
        self.event("git", why=why, ok=ok, detail=detail)

    # ------------------------------------------------------------------ budget
    def budget_exceeded(self) -> Optional[str]:
        t = self.state.data["totals"]
        started = float(self.state.data.get("relay_started_at") or time.time())
        if float(self.R["max_hours"] or 0) and (time.time() - started) / 3600.0 >= float(self.R["max_hours"]):
            return f"max_hours ({self.R['max_hours']}) reached"
        if int(self.R["max_sessions"] or 0) and t["sessions"] >= int(self.R["max_sessions"]):
            return f"max_sessions ({self.R['max_sessions']}) reached"
        if float(self.R["max_cost_usd"] or 0) and t["cost_usd"] >= float(self.R["max_cost_usd"]):
            return f"max_cost_usd (${self.R['max_cost_usd']}) reached"
        if int(self.R["max_runs"] or 0) and t["runs"] >= int(self.R["max_runs"]):
            return f"max_runs ({self.R['max_runs']}) reached"
        return None

    # ------------------------------------------------------------------ one run
    def run_once(self, agent: AgentConfig, adapter: Adapter, mode: str, session_id: str, prompt: str,
                 run_index: int, hooks_on: bool, handoff_tokens: int, hard_tokens: int, window: int,
                 kill_at_hard: bool) -> tuple:
        spec = RunSpec(mode=mode, prompt=prompt, session_id=session_id, workdir=self.workdir,
                       relay_dir=self.progress.parent, logs=self.logs, run_index=run_index,
                       label=f"relay {self.name} r{run_index}", agent=agent, hooks=hooks_on, window=window)
        launch = adapter.build(spec)
        env = procs.clean_env({
            "RELAYKIT_ACTIVE": "1", "RELAYKIT_AGENT": adapter.id, "RELAYKIT_RELAY_DIR": str(self.progress.parent),
            "RELAYKIT_PROGRESS": self._rel(self.progress), "RELAYKIT_WINDOW": str(window),
            "RELAYKIT_HANDOFF_TOKENS": str(handoff_tokens), "RELAYKIT_HARD_TOKENS": str(hard_tokens),
            "RELAYKIT_STATE_DIR": str(self.logs), "RELAYKIT_RUN": str(run_index),
            **launch.env,
        })
        out = self.logs / f"run_{run_index:03d}.jsonl"
        err = self.logs / f"run_{run_index:03d}.err.txt"
        turn = Turn(session_id=launch.session_id or session_id)
        started = time.time()
        self.event("run_start", run=run_index, agent=agent.name, mode=mode, session=turn.session_id,
                   argv=[a if len(a) < 300 else a[:300] + "..." for a in launch.argv])
        run = procs.Run(launch.argv, self.workdir, env, launch.stdin, out, err)
        self.state.set(status="running", current={"run": run_index, "agent": agent.name, "mode": mode,
                                                  "session": turn.session_id, "pid": run.pid, "started": started})
        hang_s = 60 * float(self.R["hang_minutes"])
        try:
            hung, killed, stopped_now = self._pump(run, adapter, spec, turn, window, handoff_tokens, hard_tokens,
                                                   kill_at_hard, hang_s)
        except KeyboardInterrupt:
            run.kill_tree()
            raise
        code = run.wait(timeout=30)
        stderr = run.stderr_text()
        adapter.finish(spec, turn, code, stderr)
        secs = time.time() - started
        return turn, code, stderr, secs, hung, killed, stopped_now

    def _pump(self, run, adapter, spec, turn, window, handoff_tokens, hard_tokens, kill_at_hard, hang_s) -> tuple:
        last_out = time.time()
        last_poll = 0.0
        shown_pct = -1
        hung = killed = stopped_now = False
        closed = False
        closed_at = 0.0
        while True:
            try:
                line = run.lines.get(timeout=1.0)
                if line is None:
                    closed = True
                    closed_at = time.time()
                else:
                    last_out = time.time()
                    for kind, text in adapter.on_line(line, turn):
                        self._live_event(kind, text)
            except queue.Empty:
                pass
            now = time.time()
            if now - last_poll >= 2.0:
                last_poll = now
                for kind, text in adapter.poll(spec, turn):
                    self._live_event(kind, text)
                pct = turn.pct(window)
                if pct and int(pct) != shown_pct:
                    shown_pct = int(pct)
                    toks = turn.ctx_tokens or int(pct * window / 100)
                    self.live(f"  [context] {toks:,} tokens = {pct:.1f}% of {(turn.window or window):,} "
                              f"(handoff at {100.0 * handoff_tokens / window:.0f}%)")
                    self.state.data["current"].update({"context_tokens": toks, "context_pct": round(pct, 1),
                                                       "session": turn.session_id, "tools": turn.tools})
                    self.state.save()
                if kill_at_hard and hard_tokens and (turn.ctx_tokens or pct * window / 100) >= hard_tokens and not killed:
                    self.log(f"context at {pct:.0f}% passed the hard limit and this agent has no hooks: stopping the run "
                             f"to ask for a handoff", "warn")
                    run.kill_tree()
                    killed = True
                if self.state.stop_mode() == "now" and not stopped_now:
                    self.log("stop --now requested: ending the current run", "warn")
                    run.kill_tree()
                    stopped_now = True
            if closed:
                if run.poll() is not None:
                    break
                if now - closed_at > 60:   # output closed but the process lingers
                    run.kill_tree()
                    break
            elif now - last_out >= hang_s:
                self.log(f"no output for {self.R['hang_minutes']} min: treating the run as hung and killing it", "warn")
                run.kill_tree()
                hung = True
                break
        return hung, killed, stopped_now

    def _live_event(self, kind: str, text: str) -> None:
        def short(s: str, n: int) -> str:
            s = " ".join(str(s).split())
            return s if len(s) <= n else s[: n - 3] + "..."
        if kind == "text":
            self.live(f"  agent: {short(text, 400)}")
        elif kind == "tool":
            self.live(f"  [tool] {short(text, 180)}")
        elif kind == "error":
            self.live(f"  [error] {short(text, 300)}")
        else:
            self.live(f"  {short(text, 300)}")

    # ------------------------------------------------------------------ main loop
    def run(self) -> int:
        if not self.progress.exists() or not self.prompt_file.exists():
            self.log(f"relay folder is missing {paths.PROMPT_FILE} or {paths.PROGRESS_FILE}: {self.relay_dir}", "error")
            return EXIT_ERROR
        self.setup_git()
        self.state.clear_stop()
        self.state.clear_done()
        if not self.state.data.get("relay_started_at"):
            self.state.data["relay_started_at"] = time.time()
        self.state.set(status="starting", pid=os.getpid(), workdir=str(self.workdir), progress=str(self.progress),
                       started_at=time.time())
        c = Ctx(retry_wait=float(self.R["retry_base_seconds"]))
        agent_name = self.preferred_agent()
        session_id = ""
        session_agent = ""
        takeover = ""
        last_outcome: Optional[Outcome] = None
        self.log(f"relay '{self.name}' starting in {self.workdir} — agents: {' > '.join(self.s.chain())}; "
                 f"handoff at {self.R['handoff_pct']}% / hard {self.R['hard_pct']}%")
        self.notify("started", f"relay started with {agent_name}")
        with KeepAwake(bool(self.R["keep_awake"])) as ka:
            if ka.how not in ("off",):
                self.log(f"keep-awake: {ka.how}")
            while True:
                try:
                    if self.state.stop_requested():
                        self.log("stop requested: supervisor exits (the progress file holds the state)")
                        self.state.set(status="stopped")
                        self.notify("stopped", "relay stopped on request")
                        return EXIT_STOPPED
                    if protocol.is_complete(self.progress):
                        self.log("the progress file says RELAY COMPLETE: relay finished")
                        self.state.mark_done()
                        self.state.set(status="done")
                        self.checkpoint_commit("complete")
                        self.notify("complete", f"relay finished: {protocol.summary(self.progress)['phase']}")
                        return EXIT_DONE
                    over = None if self._final_turn else self.budget_exceeded()
                    if over:
                        if c.mode in ("resume", "handoff") and session_id and not (last_outcome and last_outcome.marker):
                            self._final_turn = over   # one last turn so the session saves its progress cleanly
                            c.mode = "handoff"
                            self.log(f"budget reached ({over}): one last turn to hand off, then stopping", "warn")
                        else:
                            self.log(f"budget reached: {over}. Stopping (raise the cap and run again to continue)", "warn")
                            self.state.set(status="budget")
                            self.notify("budget", f"budget reached: {over}")
                            return EXIT_BUDGET

                    # ---- which agent
                    if not self.available(agent_name):
                        nxt = self.next_agent(agent_name) if self.s.failover.get("enabled") else None
                        if nxt:
                            self.log(f"{agent_name} is unavailable: switching to {nxt}", "warn")
                            self.notify("failover", f"switching from {agent_name} to {nxt}")
                            takeover = (f"The previous session ran on {agent_name} and was interrupted; it may have "
                                        f"stopped mid-step, so reconcile carefully before continuing. ")
                            agent_name = nxt
                        else:
                            lim = self.state.data["limited_until"]
                            waits = [float(v) for k, v in lim.items() if k in self.s.chain() and float(v) > time.time()]
                            if waits:
                                until = min(waits)
                                self.log(f"every agent is rate-limited: waiting until "
                                         f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(until))}", "warn")
                                if not self.sleep(until - time.time() + 5, "all agents rate-limited"):
                                    continue
                                continue
                            self.log(f"agent '{agent_name}' is not installed / not found on PATH", "error")
                            self.state.set(status="error")
                            self.notify("failed", f"agent {agent_name} not found")
                            return EXIT_ERROR
                    agent = self.s.agent(agent_name)
                    adapter = self.adapter_for(agent)
                    if session_agent and session_agent != agent_name:
                        c.mode = "new"
                    if c.mode != "new" and (not session_id or not adapter.caps.resume):
                        if c.mode == "handoff" and not adapter.caps.resume:
                            c.mode = "new"   # can't resume: the next fresh session reconciles from the file
                        elif not session_id:
                            c.mode = "new"
                    window = self.window_for(agent, adapter)
                    handoff_tokens, hard_tokens = self.thresholds(agent, window)
                    enforcement = (self.R.get("enforcement") or "auto").lower()
                    hooks_ready = adapter.caps.hooks or bool(getattr(adapter, "hooks_ready", lambda w: False)(self.workdir))
                    hooks_on = hooks_ready and enforcement in ("auto", "hooks")
                    checkpoint_mode = not hooks_on
                    kill_at_hard = bool(self.R["kill_at_hard"]) and adapter.caps.live_usage and not hooks_on
                    c.handoff_tokens, c.window = handoff_tokens, window
                    c.failover_available = bool(self.s.failover.get("enabled")) and self.next_agent(agent_name) is not None

                    pr = self.prompts(agent, adapter, checkpoint_mode, last_outcome.ctx_pct if last_outcome else 0.0,
                                      takeover if c.mode == "new" else "")
                    prompt = pr[c.mode]
                    t = self.state.data["totals"]
                    t["runs"] += 1
                    run_index = int(t["runs"])
                    if c.mode == "new":
                        t["sessions"] += 1
                        c.resumes = c.checkpoints = 0
                        session_id = ""
                        session_agent = agent_name
                        self.state.data["session_started_at"] = time.time()
                    self.state.save()
                    c.mode0 = c.mode
                    self.log(f"run {run_index} ({c.mode}) on {agent.name}"
                             + (f" model={agent.model}" if agent.model else "")
                             + (f" effort={agent.effort}" if agent.effort else "")
                             + f" — window {window:,}, handoff {handoff_tokens:,} / hard {hard_tokens:,} tokens, "
                             + ("hooks" if hooks_on else "checkpoint mode" + (" + kill at hard" if kill_at_hard else "")))
                    progress_mtime = self.progress.stat().st_mtime if self.progress.exists() else 0
                    turn, code, stderr, secs, hung, killed, stopped_now = self.run_once(
                        agent, adapter, c.mode, session_id, prompt, run_index, hooks_on, handoff_tokens, hard_tokens,
                        window, kill_at_hard)
                    takeover = ""
                    if turn.session_id:
                        session_id = turn.session_id
                    if turn.window:
                        self.learn_window(agent, turn.window)
                        window = turn.window
                    t["cost_usd"] = float(t["cost_usd"]) + float(turn.cost_usd or 0)
                    t["seconds"] = float(t["seconds"]) + secs
                    self.state.save()

                    # ---- outcome
                    text = turn.final_text or ""
                    tail_blob = (text[-2000:] + "\n" + " ".join(turn.errors)[-2000:] + "\n" + stderr[-2000:])
                    cls = limits.classify(tail_blob)
                    o = Outcome(
                        marker=protocol.marker_of(text),
                        complete_status=protocol.is_complete(self.progress),
                        progress_touched=(self.progress.exists() and self.progress.stat().st_mtime > progress_mtime),
                        rate_limited=turn.rate_limited, reset_at=turn.reset_at, limit_kind=turn.limit_kind,
                        context_overflow=cls["context"] and (turn.is_error or not turn.result_seen),
                        auth_error=cls["auth"] and (turn.is_error or not turn.result_seen),
                        limit_text=cls["limit"], busy=cls["busy"] and (turn.is_error or not turn.result_seen),
                        failed=turn.is_error, no_result=not turn.result_seen and not text.strip(), hung=hung,
                        killed_for_context=killed, stopped_now=stopped_now, seconds=secs,
                        ctx_tokens=turn.ctx_tokens or int(turn.pct(window) * window / 100), ctx_pct=turn.pct(window),
                        usage_known=bool(turn.ctx_tokens or turn.ctx_pct), tail=tail_blob.strip()[-400:])
                    if o.limit_text and not o.reset_at:
                        o.reset_at = limits.parse_reset(tail_blob)
                    last_outcome = o
                    self.log(f"run {run_index} ended after {int(secs)} s (exit {code}, {turn.tools} tools, context "
                             f"{o.ctx_pct:.0f}%, cost ${turn.cost_usd:.2f}). Marker: {o.marker or 'none'}")
                    self.event("run_end", run=run_index, agent=agent.name, exit=code, seconds=round(secs),
                               marker=o.marker, ctx_tokens=o.ctx_tokens, ctx_pct=round(o.ctx_pct, 1),
                               cost=turn.cost_usd, rate_limited=o.rate_limited, reset_at=o.reset_at,
                               session=session_id, tools=turn.tools, failed=o.failed, hung=o.hung)

                    a = decide(o, c, self.s)
                    self.log(f"-> {a.kind}: {a.reason}")

                    # bookkeeping shared by several actions
                    if o.marker == protocol.BLOCKED:
                        c.blocked_streak += 1
                        self.notify("blocked", f"agent reported RELAY BLOCKED: {protocol.section(self.progress, 'Blockers', 400)}")
                    elif o.marker:
                        c.blocked_streak = 0
                    if o.marker == protocol.HANDOFF:
                        c.stalled = 0 if o.progress_touched else c.stalled + 1
                        if not o.progress_touched:
                            self.log("warning: handoff printed but the progress file wasn't modified in that session", "warn")
                        self.checkpoint_commit("handoff")
                        if c.stalled >= int(self.R["max_stalled_sessions"]):
                            self.log(f"{c.stalled} sessions in a row handed off without updating the progress file: "
                                     f"stopping so a person can look", "error")
                            self.state.set(status="stalled")
                            self.notify("failed", "relay stalled: sessions are handing off without making progress")
                            return EXIT_BLOCKED

                    if self._final_turn and a.kind != "done":
                        self.state.set(status="budget")
                        self.notify("budget", f"budget reached: {self._final_turn}")
                        return EXIT_BUDGET

                    if a.kind == "done":
                        self.state.mark_done()
                        self.state.set(status="done")
                        self.checkpoint_commit("complete")
                        self.notify("complete", "relay finished: RELAY COMPLETE")
                        return EXIT_DONE
                    if a.kind == "stop":
                        self.state.set(status="stopped" if a.exit_code == EXIT_STOPPED else "blocked")
                        if a.exit_code != EXIT_STOPPED:
                            self.notify("blocked" if a.exit_code == EXIT_BLOCKED else "failed", a.reason)
                        return a.exit_code
                    if a.kind == "failover":
                        until = o.reset_at if (o.reset_at and o.reset_at > time.time()) else \
                            time.time() + 60 * float(self.R["limit_wait_minutes" if not o.auth_error else "auth_wait_minutes"])
                        self.state.data["limited_until"][agent.name] = until
                        self.state.save()
                        nxt = self.next_agent(agent.name)
                        self.notify("limit" if not o.auth_error else "auth",
                                    f"{agent.name}: {a.reason}; available again ~{time.strftime('%H:%M', time.localtime(until))}")
                        if nxt:
                            self.notify("failover", f"continuing on {nxt} while {agent.name} is unavailable")
                            takeover = (f"The previous session ran on {agent.name} and was interrupted by a {a.reason}; it "
                                        f"may have stopped mid-step, so reconcile carefully before continuing. ")
                            agent_name = nxt
                        c.mode = "new"
                        continue
                    if a.kind == "wait":
                        if o.rate_limited or o.limit_text:
                            self.state.data["limited_until"][agent.name] = time.time() + a.wait_seconds
                            self.state.save()
                            self.notify("limit", f"{agent.name}: {a.reason} — resuming around "
                                                 f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(time.time() + a.wait_seconds))}")
                        if o.auth_error:
                            c.auth_waits += 1
                            self.notify("auth", f"{agent.name}: {a.reason}")
                        if not a.count_run:
                            t["runs"] -= 1
                            self.state.save()
                        self.log(f"waiting {a.wait_seconds / 60:.1f} min ({a.reason})")
                        if not self.sleep(a.wait_seconds, a.reason):
                            continue
                        self.state.data["limited_until"].pop(agent.name, None)
                        self.state.save()
                        if a.then == "resume" and (o.hung or o.failed or o.no_result or o.busy) and not (o.rate_limited or o.auth_error):
                            c.retry_wait = min(c.retry_wait * 2, float(self.R["retry_max_seconds"]))
                        c.mode = a.then if (a.then == "new" or session_id) else "new"
                        continue
                    # normal transitions
                    c.retry_wait = float(self.R["retry_base_seconds"])
                    c.auth_waits = 0
                    if a.kind == "new":
                        if self.s.failover.get("switch_back", True):
                            pref = self.preferred_agent()
                            if pref != agent_name:
                                self.log(f"switching back to {pref} for the next session")
                                agent_name = pref
                        c.mode = "new"
                    elif a.kind == "resume":
                        if o.marker == protocol.CHECKPOINT:
                            c.checkpoints += 1
                        else:
                            c.resumes += 1
                        c.mode = "resume"
                    elif a.kind == "handoff":
                        c.mode = "handoff"
                    self.sleep(float(self.R["gap_seconds"]))
                except KeyboardInterrupt:
                    self.log("interrupted (Ctrl+C): supervisor exits; run `relaykit run` again to continue")
                    self.state.set(status="stopped")
                    return EXIT_STOPPED
                except Exception as exc:  # a bug or an odd stream must not end the relay
                    self.log(f"supervisor error: {exc!r}\n{traceback.format_exc()[-1500:]}", "error")
                    self.event("supervisor_error", error=repr(exc))
                    if not self.sleep(60, "recovering from a supervisor error"):
                        continue
                    c.mode = "new"
