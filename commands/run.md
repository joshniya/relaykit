---
description: Start (or resume) a relaykit relay in the background after confirming agents, models and effort
argument-hint: "<relay-name> [--agent X] [--failover a,b] [--git worktree]"
---
Start a relaykit relay. Follow the **relaykit-run** skill.

1. Run `relaykit list` and `relaykit run <name> --show <flags>` (with the flags from the arguments) to see the
   setup: agent chain, model + effort per agent, context limits, budget, git, notifications.
2. Show me that setup and ask me to confirm it — especially the model and reasoning effort of EVERY agent in the
   chain (including failover agents). If any agent shows "(CLI default)" or isn't confirmed, ask me what to use and
   save it with `relaykit agents <name> --model <m> --effort <e>`. Never pick a model or effort for me.
3. Once I confirm, run `relaykit run <name> --detach --yes <flags>` and tell me how to follow it
   (`relaykit watch <name>` in a terminal, or `/relaykit:status <name>` here).

Arguments: $ARGUMENTS
