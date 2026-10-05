# relaykit (Gemini CLI extension)

relaykit runs coding agents on projects too big for one context window: a chain of fresh sessions, each
handing off through `RELAY_PROGRESS.md`. Commands: `/relaykit:init`, `/relaykit:plan`, `/relaykit:run`,
`/relaykit:status`, `/relaykit:stop`. The skills `relaykit-init`, `relaykit-plan` and `relaykit-run` hold the
procedures. The `relaykit` CLI must be installed (`pipx install relaykit`).

If your prompt says you are "a session of an unattended relay", ignore this file and follow the relay's
RELAY_PROMPT.md exactly.
