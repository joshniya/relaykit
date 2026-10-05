# Contributing

Thanks for helping! relaykit is pure Python 3.9+ with **no runtime dependencies** — please keep it that way
(the Claude Code plugin bundles the source and runs it with whatever Python is on PATH).

## Setup
```bash
git clone https://github.com/Jishwuh/relaykit && cd relaykit
python -m pip install pytest pyflakes
python -m pytest            # unit + end-to-end (a scripted fake agent CLI; no API calls)
python -m pyflakes src tests
python tools/sync_assets.py --check
```

## Where things live
- `src/relaykit/supervisor.py` — the loop and the pure `decide()` table
- `src/relaykit/adapters/` — one file per agent CLI family (see docs/ADAPTERS.md)
- `src/relaykit/hooks.py` — the in-session context hook (shared by every agent with hooks)
- `src/relaykit/templates/` — RELAY_PROMPT / RELAY_PROGRESS / PLAN templates
- `src/relaykit/assets/skills/` — canonical skills; `skills/` at the root is a COPY for the Claude plugin and the
  Gemini extension (`python tools/sync_assets.py` after editing a skill)
- `commands/*.md` (Claude Code) and `commands/relaykit/*.toml` (Gemini CLI) — slash commands

## Adapters
New or fixed adapters are the most valuable contribution. Include recorded output in tests and, if you can, run
the toy relay (`examples/toy/`) against the real CLI and say so in the PR — that's what moves an adapter from
"beta" to "tested".

## Pull requests
Small and focused; tests for behaviour changes; a line in CHANGELOG.md.
