# The relay protocol

The protocol is deliberately small and agent-agnostic: any coding agent that can read and write files and print
text can take part. relaykit is one supervisor for it; you could write another.

## Roles

- **Supervisor** — starts agent processes, watches them, decides what happens after each one ends. Never edits
  the progress file except to create it.
- **Session** — one agent conversation. It may span several *runs* (processes) when the supervisor resumes it.
- **Owner** — the human. Answers questions before the relay (in the plan), does the owner-only items after.

## Files

| File | Written by | Purpose |
|---|---|---|
| `PLAN.md` | the planner (human + agent) before the relay | goal, **locked decisions**, phases with Definitions of done, constraints |
| `RELAY_PROMPT.md` | relaykit (template) + owner | the rules every session follows (reading order, reconcile, cadence, handoff, markers, finishing) |
| `RELAY_PROGRESS.md` | sessions | the ONLY state: Current table, Next action, Phases, Baseline, Orientation notes, Traps, Files touched, Decision log, Owner handoff, Blockers, Session log, Final report |
| `.relaykit/PROJECT.md` | `relaykit scan` + init session | how to build/test/run this codebase, baseline, conventions, hazards |

### The Current table

```markdown
## Current
| Field | Value |
|---|---|
| Status | IN PROGRESS |
| Session | 7 |
| Phase | Phase 3 (money) — IN PROGRESS |
| Step | wiring the refund path; tests for partial refunds next |
| Last updated | 2026-10-05 session 7 |
```

`Status` is one of `IN PROGRESS`, `BLOCKED`, `RELAY COMPLETE`. The supervisor reads it (regex
`^\| Status \| (.*) \|$`) to confirm completion.

## Markers

The **last non-empty line** of every reply is exactly one marker (markdown decoration around it is ignored; a
marker anywhere else in the reply means nothing):

| Marker | The session promises | The supervisor then |
|---|---|---|
| `RELAY HANDOFF` | the progress file is saved: Current, Next action (concrete), phase rows, files touched, decisions, a session-log line ending in `handoff` | starts a **fresh** session |
| `RELAY COMPLETE` | every phase is done, the Final report is written, Status is `RELAY COMPLETE` | verifies Status, then stops (otherwise resumes the session to fix it) |
| `RELAY BLOCKED` | it cannot continue without the owner; the reason is under "Blockers" | waits, then tries a fresh session; after N in a row, stops and notifies |
| `RELAY CHECKPOINT` | (checkpoint mode only) a step is done and progress saved | resumes with "continue", or asks for a handoff if context is past the handoff point |

A turn that ends with **no marker** is resumed with "continue"; after `max_resumes_per_session` the supervisor
asks for a handoff, and if even that turn has no marker it starts a fresh session anyway — the progress file is
the state.

## Context discipline

Two thresholds per agent: **handoff** (default 35% of the window, optionally capped in tokens) and **hard**
(default 45%). Three enforcement modes, chosen per agent run:

1. **Hooks** (Claude Code; Gemini once its hooks are installed). The agent's own hook system calls relaykit's
   hook script: after each tool call past *handoff* it injects "finish your step and hand off"; before each tool
   call past *hard* it refuses everything except reading/editing the relay files; when the agent tries to end a
   turn without a marker, the stop gate refuses (up to a limit, then the supervisor takes over).
2. **Live usage without hooks** (Codex, Kiro, Qwen, Amp, opencode, Copilot): the agent works in checkpoint mode;
   the supervisor watches usage and, if a run passes *hard*, stops the process and resumes the session with
   "hand off now".
3. **No usage at all** (aider, custom CLIs): checkpoint mode with a fixed number of steps per session.

Sessions are also told to hand off right after finishing a phase if they're past ~20%, so phases start fresh.

## Session procedure (what RELAY_PROMPT.md asks)

1. Read the progress file, the project profile, the plan (fully in session 1; the current phase later).
2. Increment Session; log `session N started (phase X, agent Y)`.
3. **Reconcile**: the previous session may have died mid-edit or been another agent; check `git status`/diff for
   the files it touched; finish or undo half-done work (only the relay's own).
4. Work the phase; save progress after every meaningful step and before any long operation.
5. Decisions the plan doesn't cover: choose the safest option, log it, continue. Never re-open a locked decision.
6. Owner-only actions (deploy, publish, push, secrets, production, paid services): never — log under Owner handoff.
7. Foreground only; commands under ~10 minutes; compare test results with the recorded baseline.
8. Hand off cleanly; finish with a Final report.

## Failover between agents

Because the state is in files, any agent can take over. When the supervisor switches agents (rate limit, sign-out)
it starts a **fresh** session on the next agent with a note that the previous session was interrupted and may
have stopped mid-step, so reconciling matters. When the original agent's limit resets, the next fresh session
goes back to it (`switch_back`).
