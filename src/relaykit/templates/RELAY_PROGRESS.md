# {{title}}: relay progress
Every session: read `{{prompt_rel}}`, then this whole file.

## Current
| Field | Value |
|---|---|
| Status | IN PROGRESS |
| Session | 0 |
| Phase | {{first_phase}} — NOT STARTED |
| Step | Relay created; no work yet |
| Last updated | {{today}} (relay created) |

Status values: IN PROGRESS | BLOCKED | RELAY COMPLETE

## Next action
{{next_action}}

## Phases
| # | Phase | Status | Notes |
|---|---|---|---|
{{phase_rows}}

## Baseline
### Repository state before any work
(fill in: `git status --short`, current commit, anything already modified that is NOT this relay's)

### Build / test baseline
| Check | Command | Result |
|---|---|---|
| | | |

## Orientation notes
(facts future sessions need, with file:line references — keep current; cheaper than re-reading big files)

## Traps
(things that bit or will bite — add as found)

## Files touched
(every file this relay creates or edits, with the phase)

## Decision log
(one line each: what, why)

## Owner handoff
| What | Where / exact value | Why |
|---|---|---|

## Blockers
(none)

## Session log
- {{today}}: relay created by relaykit.
