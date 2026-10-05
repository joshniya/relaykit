# Toy relay: a 2-minute smoke test for any agent

Three tiny phases, each writing one file. With a low token cap every phase lands in its own session, so you see
real handoffs, resumes and the completion check — for a few cents.

```bash
cp -r examples/toy /tmp/toy && cd /tmp/toy && git init -q && git add -A && git commit -qm init
relaykit scan
relaykit new toy --plan plan.md --no-final-review
# force handoffs: a fresh Claude Code session starts around 30-45k tokens, a Codex session around 16k
relaykit agents claude --model claude-haiku-4-5 --effort low --handoff-tokens-max 34000
relaykit run toy --yes
# or with Codex:
relaykit agents codex --model <a light model> --effort low --handoff-tokens-max 19000
relaykit run toy --agent codex --yes
```

Expected: 3-5 runs, `RELAY HANDOFF` between phases, then `RELAY COMPLETE`; `notes/one.md`, `notes/two.md` and
`notes/summary.md` exist and the progress file's session log shows one line per session.
