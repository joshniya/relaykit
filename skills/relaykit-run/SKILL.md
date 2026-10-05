---
name: relaykit-run
description: Start, watch, troubleshoot and stop relaykit relays (unattended back-to-back agent sessions that build a plan with file-based handoffs). Use when the user asks to run/start/resume a relay, check how it's going, why it stopped, change its agent/model, or stop it; or invokes /relaykit:run, /relaykit:status, /relaykit:stop.
license: MIT
compatibility: Needs the `relaykit` CLI (pipx install relaykit) and at least one supported agent CLI (claude, codex, gemini, kiro, qwen, amp, cursor-agent, opencode, copilot, aider).
metadata:
  project: relaykit
  version: "0.1.0"
---

# Operate relays

## Never run a relay from inside a relay session
If you are yourself a relay session (your prompt says "You are a session of an unattended relay"), do not use
this skill — follow RELAY_PROMPT.md instead.

## Commands
| Goal | Command |
|---|---|
| What's installed / healthy | `relaykit doctor`, `relaykit adapters` |
| Relays in this project | `relaykit list`, `relaykit status [name]` (`--json` for machines) |
| Start in the foreground | `relaykit run <name>` |
| Start in the background | `relaykit run <name> --detach` (Windows: add `--window` for a visible console) |
| Follow it live | `relaykit watch <name>` · last lines: `relaykit logs <name> -n 100` |
| Stop after the current run | `relaykit stop <name>` · immediately: `relaykit stop <name> --now` |
| Change agent/model for a run | `relaykit run <name> --agent codex --model <m> --effort high` |
| Confirm/edit agents | `relaykit agents` (model + reasoning effort per agent; saved as confirmed) |
| Failover chain for a run | `relaykit run <name> --failover codex,gemini` |
| Budget caps | `--max-hours 8 --max-sessions 30 --max-cost-usd 50` (`--reset-budget` to start counting again) |
| Git checkpoints | `--git worktree` (own branch + worktree, commit per handoff) or `--git commit` |
| Test notifications | `relaykit notify-test` |

A relay can also be any folder with `RELAY_PROMPT.md` + `RELAY_PROGRESS.md`: `relaykit run path/to/folder`.

## Before starting
- `relaykit run` shows the full setup (agents with model + effort, limits, budget, git, notifications) and asks
  before starting. If an agent's model/effort was never confirmed it asks for that too — **always let the user
  confirm these; never choose a model or effort for them.**
- Make sure the plan's open questions are answered: a relay can't ask anyone.

## Reading status
`relaykit status <name>` shows the progress file's Status / Phase / Step, what the supervisor is doing now (run,
agent, context %), waits (rate limit until <time>), and totals. The authoritative state is always the relay's
`RELAY_PROGRESS.md`: read its "Next action", "Decision log", "Owner handoff" and "Blockers".

## Troubleshooting
| Symptom | What to do |
|---|---|
| `blocked` / RELAY BLOCKED | Read "Blockers" in the progress file; fix what it needs (tool, secret, decision), then `relaykit run <name>` |
| `stalled` | Sessions handed off without updating the progress file. Read the last session's log (`relaykit logs`), fix the cause (often a broken prompt/plan or a crash loop), run again |
| Waiting on a usage limit | Nothing to do: it resumes at the reset time. Add `--failover <agent>` to keep going on another agent |
| Signed out / auth errors | Sign the agent CLI in again in a terminal (`claude` → /login, `codex login`, ...). The relay retries |
| `budget` | A cap was reached. Raise it in config or with flags and run again (`--reset-budget` restarts the count) |
| Context window warnings | The window is learned after the first run; set `context_window` in `[agents.<name>]` to fix it up front |
| Edited the plan mid-relay | Fine: stop it, edit PLAN.md (and the progress file's Next action if needed), run again |

When the relay finishes, the progress file's "Final report" lists what was built, test results, decisions taken,
and **"What you need to do"** (owner-only steps). Summarise that for the user. In worktree mode the work is on
branch `relaykit/<name>` in a sibling folder — the user reviews and merges it.
