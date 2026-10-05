---
description: Profile this codebase for relaykit (scan, verify build/test commands, record the test baseline)
argument-hint: "[--deep]"
---
Set up relaykit in this project.

1. Run `relaykit scan` (via the Bash tool) to write `.relaykit/project.json` and `.relaykit/PROJECT.md`. If the
   `relaykit` command isn't found, tell me to install it (`pipx install relaykit` or `uv tool install relaykit`)
   and stop.
2. Follow the **relaykit-init** skill to verify the commands, record the test baseline, and write the architecture
   notes, conventions and hazards into `.relaykit/PROJECT.md` below `<!-- relaykit:keep -->`.
3. Run `relaykit doctor` and show me which agent CLIs are installed. Then ask me which agent should start relays,
   whether to fail over to others while it's rate-limited, and — for EVERY agent I pick — which model and reasoning
   effort to use (show me the options; never choose for me). Save each with
   `relaykit agents <name> --model <model> --effort <effort>`.
4. Ask whether I want git checkpoints (own worktree + branch, recommended), notifications (desktop / ntfy topic /
   webhook URL) and budget caps; write them into `.relaykit/config.toml` (`[git] mode`, `[notify]`, `[relay] max_*`).

Arguments: $ARGUMENTS
