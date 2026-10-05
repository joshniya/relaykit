# relaykit

**Run any AI coding agent on projects too big for one context window — unattended, across as many sessions as it
takes, across agents.**

relaykit turns a plan into a *relay*: a chain of fresh agent sessions. Each session works until its context
reaches a set point (35% by default), writes a handoff into a progress file, and ends with a marker line. A
supervisor starts the next session, which picks up exactly where the last one stopped. Usage limits, crashes,
hangs, sign-outs and full context windows are handled for you, so a big plan keeps moving while you sleep.

```
relaykit init                                  # profile the codebase, confirm agents/models
relaykit plan payments-v2 --goal "..."         # plan it WITH an agent: it researches and interviews you
relaykit run payments-v2 --detach              # run it unattended; come back to a final report
relaykit watch payments-v2                     # follow it live
```

Works with **Claude Code** and **OpenAI Codex CLI** (both live-tested), plus **Gemini CLI, Kiro, Qwen Code, Amp,
Cursor CLI, opencode, GitHub Copilot CLI, aider** (beta) and **any other CLI** you describe in config. Runs on
Windows, macOS and Linux. Pure Python 3.9+, zero dependencies.

---

## Why

Long tasks fail in predictable ways when you hand them to one agent session:

- **Context rot.** Quality drops long before the window is full, and auto-compaction throws detail away.
- **Stops.** Usage limits, overloaded APIs, a hung tool, an expired login — and nobody is there to press enter.
- **Drift.** Without a written plan and state, the next session redoes or undoes work.

relaykit fixes all three with a simple, agent-agnostic protocol: **state lives in files, not chat.** Every session
reads `RELAY_PROMPT.md` (the rules) and `RELAY_PROGRESS.md` (the state), and must end every turn with one of:

| Marker (last line of the reply) | Meaning |
|---|---|
| `RELAY HANDOFF` | progress saved — start a fresh session |
| `RELAY COMPLETE` | the plan is done **and** the progress file says `RELAY COMPLETE` |
| `RELAY BLOCKED` | impossible without the owner (reason written under "Blockers") |
| `RELAY CHECKPOINT` | a step is done (agents that can't be watched mid-turn) — supervisor says continue or hand off |

## What it handles for you

| Problem | What relaykit does |
|---|---|
| Context filling up | Measures each session's context live. Claude Code (and Gemini with hooks installed) gets mid-turn hooks: a warning past the handoff point, a refusal of every tool but the handoff past the hard point, and a stop gate that won't let a turn end without a marker. Other agents work in checkpoint steps, and the supervisor stops a run at the hard limit and asks for a handoff. Percentages **and** absolute token caps (important for 1M-token models). |
| Usage limits | Reads the reset time from the CLI (structured events from Claude/Codex, or prose like "try again at 3:42 PM") and sleeps until then — or a fallback timer when there's no time. With **failover**, it continues on the next agent and switches back after the reset. |
| Crashes, overloads, hangs | Exponential back-off on 5xx/overloaded, kills runs that go silent, resumes the same session, starts fresh when a resume fails, restarts the supervisor itself when detached. |
| Sign-outs | Waits and retries (or fails over) and notifies you. |
| Turns that end without a marker | Resumes the same session with "continue"; after N tries asks for a handoff. |
| Sessions that make no progress | Circuit breaker: stops after N handoffs that didn't touch the progress file. |
| Knowing the codebase | `relaykit scan` profiles any repo (manifests, scripts, CI, instruction files, hazards) into `.relaykit/PROJECT.md`; the init skill verifies the commands and records the test baseline. |
| Planning | The plan skill researches the code, interviews you only about real decisions, and writes a phased plan with **locked decisions** and Definitions of done — because nobody can answer questions mid-relay. |
| Choosing models | Never silent: every agent (including failover agents) has its model and reasoning effort confirmed once, and every run shows the full setup before starting. |
| Rolling back | Git checkpoints: run in its own worktree + branch and commit at every handoff. |
| Knowing when it's done | Desktop notifications, ntfy.sh phone push, Discord/Slack/any webhook. |
| Cost | Budget caps: hours, sessions, runs, cost-equivalent USD. |
| Your laptop sleeping | Keep-awake while a relay runs (Windows, macOS, Linux). |

## Install

```bash
pipx install relaykit          # or: uv tool install relaykit   /   pip install relaykit
relaykit doctor                # checks Python, agents, git, keep-awake
```

**Claude Code plugin** (adds `/relaykit:init`, `/relaykit:plan`, `/relaykit:run`, `/relaykit:status`, `/relaykit:stop`
and the three skills; bundles the CLI so it works even without pipx):

```
/plugin marketplace add joshniya/relaykit
/plugin install relaykit@relaykit
```

**Gemini CLI extension** (same commands as TOML + skills):

```bash
gemini extensions install https://github.com/joshniya/relaykit
```

**Codex, Cursor, Copilot, opencode, Amp, Kiro, Qwen** — install the skills into the folders they read
(`.agents/skills` is the shared one):

```bash
relaykit install --for all --scope user      # or --scope project
```

## Quick start

```bash
cd your-project
relaykit init            # scan + pick agents + confirm model/effort + git/notify/budget (writes .relaykit/)
relaykit init --deep     # optional: an agent runs your build/tests and records the baseline
relaykit plan my-feature --goal "Add per-tenant rate limiting to the public API"
#   -> opens your agent interactively; it researches, asks you the real questions, writes
#      .relaykit/relays/my-feature/PLAN.md and creates the relay folder
relaykit run my-feature --detach
relaykit watch my-feature          # Ctrl+C stops watching, not the relay
```

Already have a plan? `relaykit new my-feature --plan path/to/plan.md`. Already have a relay folder from an older
setup (any folder with `RELAY_PROMPT.md` + `RELAY_PROGRESS.md`)? `relaykit run path/to/folder`.

When it finishes, `RELAY_PROGRESS.md` ends with a **Final report**: what was built, test results against the
baseline, every decision taken on its own, review findings, and **"What you need to do"** (deploys, secrets,
publishing — the things a relay never does itself).

## Commands

| Command | What it does |
|---|---|
| `relaykit doctor` / `adapters` | health check / supported agents and their capabilities |
| `relaykit scan [--refresh]` | profile the codebase into `.relaykit/PROJECT.md` + `project.json` |
| `relaykit init [--deep]` | scan, configure agents (model + effort), failover, git, notifications, budget |
| `relaykit agents [names] [--model M --effort E]` | confirm or change an agent's model / reasoning effort / options |
| `relaykit plan <name> --goal "..."` | plan interactively with an agent (relaykit-plan skill) |
| `relaykit new <name> [--plan FILE]` | create a relay folder (PLAN.md, RELAY_PROMPT.md, RELAY_PROGRESS.md) |
| `relaykit run <name> [--detach]` | run it (`start` = run --detach; `--show` prints the setup and exits) |
| `relaykit status [name] [--json]` / `list` | where each relay is |
| `relaykit watch <name>` / `logs <name>` | follow live / print the log tail |
| `relaykit stop <name> [--now]` | stop after the current run, or immediately |
| `relaykit install --for all` | install the skills for every agent |
| `relaykit notify-test` | send a test notification |

Useful `run` flags: `--agent codex --model gpt-5.6-sol --effort high`, `--failover codex,gemini`,
`--git worktree`, `--max-hours 8 --max-sessions 30 --max-cost-usd 50`, `--handoff-pct 30 --hard-pct 40`,
`--accept-defaults` (use each CLI's own model defaults without confirming), `--window` (Windows: visible console).

## How context is enforced, per agent

| Agent | Session id | Live usage | Mid-turn enforcement | Status |
|---|---|---|---|---|
| Claude Code (`claude -p`) | preset | stream | hooks (warn / deny / stop gate) | tested |
| Codex CLI (`codex exec`) | `thread.started` | session rollout file | checkpoints + stop at hard limit | tested |
| Gemini CLI | `init` event | via hooks (`relaykit install --gemini-hooks`) | hooks when installed, else checkpoints | beta |
| Kiro CLI | stream | `contextUsagePercentage` | checkpoints + stop at hard limit | beta |
| Qwen Code, Amp | stream | stream | checkpoints + stop at hard limit | beta |
| opencode, Copilot CLI | stream | stream | checkpoints + stop at hard limit | beta |
| Cursor CLI | stream | end of turn | checkpoints | beta |
| aider, custom CLIs | — | — | checkpoints (fixed number of steps per session) | beta |

"Beta" adapters are built from each CLI's documentation and tested against recorded output, not yet against the
live CLI. Reports and fixes welcome — see [docs/ADAPTERS.md](docs/ADAPTERS.md) to add or fix one.

## Files

```
.relaykit/
  config.toml            agents (model, effort, confirmed), limits, failover, git, notify, budget
  PROJECT.md             codebase profile every session reads first
  project.json           the raw scan
  relays/<name>/
    PLAN.md              goal, locked decisions, phases with Definitions of done
    RELAY_PROMPT.md      the rules every session follows
    RELAY_PROGRESS.md    the live state (Current, Next action, Phases, Decision log, Owner handoff, ...)
    relay.toml           optional per-relay overrides
  logs/<name>/           live.log, events.jsonl, run_NNN.jsonl, state.json (gitignored)
```

## Safety

Relay sessions run **without permission prompts** (`--dangerously-skip-permissions`, `--dangerously-bypass-approvals-and-sandbox`,
`--yolo`, ...) because nobody is there to approve them. Read [docs/SAFETY.md](docs/SAFETY.md). In short: use
`git.mode = "worktree"`, keep secrets out of the repo, prefer a container/VM for untrusted plans, and remember that
relay rules tell agents to never deploy, publish, push or touch production — but rules are not a sandbox.

## Docs

- [docs/PROTOCOL.md](docs/PROTOCOL.md) — the relay protocol (markers, progress file, handoffs), agent-agnostic
- [docs/CONFIG.md](docs/CONFIG.md) — every setting
- [docs/ADAPTERS.md](docs/ADAPTERS.md) — how each agent is driven; writing a new adapter or a custom CLI
- [docs/SAFETY.md](docs/SAFETY.md) — running unattended agents responsibly
- [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) — the supervisor's decision loop

## Prior art and thanks

relaykit stands on ideas from Anthropic's
[effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
(progress files, fresh sessions), the Ralph loop family ([ghuntley](https://ghuntley.com/ralph/),
[ralph-claude-code](https://github.com/frankbria/ralph-claude-code) — circuit breakers, two-condition exits),
[claude-auto-resume](https://github.com/terryso/claude-auto-resume), and Amp's
[handoff](https://ampcode.com/news/handoff). What relaykit adds: context-%-driven handoffs enforced inside the
session, one protocol across many agent CLIs, cross-agent failover, codebase profiling, interview-first planning,
and the operational pieces (reset-time waits, budgets, notifications, git worktree checkpoints).

## License

MIT
