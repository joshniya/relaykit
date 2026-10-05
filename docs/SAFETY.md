# Running unattended agents responsibly

A relay runs coding agents **with their permission prompts turned off** — nobody is there to click "allow".
That's the point, and it's also the risk. relaykit's rules tell every session never to deploy, publish, push,
touch production, handle secrets or spend money, and to log those as owner-handoff items instead. **Rules are not
a sandbox.** Treat a relay like giving a capable contractor a shell on your machine overnight.

## Recommended setup

1. **Use `git.mode = "worktree"`.** The relay works on its own branch in its own folder; your working tree is never
   touched, every handoff is a commit, and you review a branch in the morning.
2. **No production credentials in reach.** Don't run relays in a shell or machine that has prod cloud credentials,
   deploy keys or a production `.env` loaded. Use test/staging keys or none.
3. **Prefer isolation for anything you didn't write the plan for**: a container, a VM, a devcontainer, or a cloud
   sandbox. Codex's own docs describe its bypass flag as intended for externally sandboxed environments.
4. **Budget caps.** Set `max_hours` / `max_sessions` / `max_cost_usd` so a confused relay can't run forever.
5. **Notifications.** Turn on `blocked`, `failed` and `auth` so you hear about problems quickly.
6. **Read the plan's constraints.** Put the never-touch list in the plan ("don't edit migrations", "don't run the
   e2e suite against staging") — sessions follow the plan's constraints closely.

## What relaykit itself does and doesn't do

- It **never** pushes, merges, deploys or publishes. Git checkpoints only commit locally (to the relay's branch in
  worktree mode), never skip your git hooks, and never rewrite history.
- It strips "nested session" environment variables so a relay started from inside an IDE agent behaves like a
  terminal run; it does **not** strip API keys or credentials the agent CLI needs.
- Its hooks only act when `RELAYKIT_ACTIVE=1` (set for relay runs), so installing them never changes your normal
  interactive sessions.
- Notification payloads contain the relay name, the event and a one-line message — never code or file contents.

## Shared working trees

If other people or agents edit the same folder while a relay runs (not recommended), keep `git.mode = "off"` and
say so in the plan's constraints: the session rules already tell agents to never revert or sweep up changes they
can't attribute to the relay, and to edit with targeted replacements.
