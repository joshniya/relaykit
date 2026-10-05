# Security

relaykit runs coding agents without permission prompts by design. Please read [docs/SAFETY.md](docs/SAFETY.md)
before running relays, and run them in a git worktree, a container or a VM when the plan isn't fully yours.

## Reporting a vulnerability
Please don't open a public issue for security problems. Use GitHub's private vulnerability reporting
("Security" tab, then "Report a vulnerability") on https://github.com/joshniya/relaykit.

In scope: relaykit executing something the user didn't configure; leaking file contents or credentials into
notifications or anywhere outside `.relaykit/logs/`; hooks acting outside relay runs (`RELAYKIT_ACTIVE` unset);
path or argument injection through relay names, plans or agent output.
