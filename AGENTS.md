# Working on relaykit (for coding agents)

- Pure Python 3.9+, standard library only. Use `from __future__ import annotations` in modules with modern type
  syntax. No new runtime dependencies.
- Before finishing: `python -m pytest`, `python -m pyflakes src tests`, `python tools/sync_assets.py --check`.
  Tests never call a real agent API; the end-to-end tests use `tests/fake_agent.py`.
- Skills: edit `src/relaykit/assets/skills/`, then run `python tools/sync_assets.py` (root `skills/` is a copy).
- Keep the supervisor's `decide()` pure and covered by `tests/test_decide.py`.
- Adapters must never put free text into a Windows `.cmd` shim's argv (use `resolve_npm_shim` or stdin).
- Never weaken: the marker-on-the-last-line rule, the COMPLETE + Status double check, env-gated hooks
  (`RELAYKIT_ACTIVE`), or "never pick a model / reasoning effort silently".
