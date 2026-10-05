# How the supervisor works

```
relaykit run
  └─ guard (when --detach): restarts the supervisor if it crashes before the relay is done
      └─ supervisor loop (src/relaykit/supervisor.py)
           ├─ stop requested?  progress says RELAY COMPLETE?  budget reached?
           ├─ pick an agent: the first available in the failover chain (installed, not rate-limited)
           ├─ build the run: new session | resume ("continue") | forced handoff turn
           │    env RELAYKIT_* (thresholds, relay paths) → the agent's hooks read them
           ├─ spawn the CLI; stream its output line by line
           │    adapter.on_line → session id, usage, text, tools, rate limits, errors
           │    every 2 s: adapter.poll (side channels), live context %, hard-limit stop, stop --now, hang check
           ├─ outcome: marker on the last line, Status in the progress file, limits, errors, hang...
           └─ decide(outcome) → done | new | resume | handoff | wait(then) | failover | stop
```

## The decision table (in order)

1. `stop --now` → stop.
2. `RELAY COMPLETE` → done if the progress file's Status agrees, else resume to fix it.
3. `RELAY HANDOFF` → fresh session (git checkpoint; stalled-session circuit breaker; switch back to the preferred
   agent if its limit reset).
4. `RELAY BLOCKED` → wait, then a fresh session; stop after `max_blocked` in a row.
5. `RELAY CHECKPOINT` → continue, or ask for a handoff when past the handoff point / out of steps.
6. Run stopped at the hard limit → forced handoff turn.
7. Usage limit → fail over if another agent is available; else wait until the reported reset (+ margin) or the
   fallback timer, then resume the same session.
8. "Context too long" → fresh session.
9. Signed out → fail over, or wait and retry (bounded).
10. Error / overloaded / hung / no result → back off (doubling) and resume; a resume that fails instantly → fresh.
11. Forced handoff turn without a marker → fresh session anyway.
12. No marker → resume ("continue"); after `max_resumes_per_session`, a forced handoff turn.

`decide()` is a pure function (`tests/test_decide.py` covers it); `tests/test_supervisor_e2e.py` drives the real
loop with a scripted fake agent for handoffs, resumes, rate-limit waits, failover, blocked/stalled stops, budgets,
hangs and stop requests.

## State on disk

`.relaykit/logs/<relay>/`:
- `live.log` — what `relaykit watch` shows (agent text, tools, context %, decisions)
- `events.jsonl` — machine-readable: run_start / run_end (exit, marker, context, cost, limits) / notify / git
- `run_NNN.jsonl`, `run_NNN.err.txt` — each run's raw output
- `state.json` — supervisor status, current run, totals (budget), rate-limited agents, learned windows
- `STOP` (stop request), `RELAY_DONE` (finished)

The progress file is the relay's real state, so you can stop, edit the plan or the progress file's Next action,
and run again at any time.
