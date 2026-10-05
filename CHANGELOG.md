# Changelog

## 0.1.0 — 2026-10-05

First public release.

- Supervisor: back-to-back sessions with file-based handoffs; markers HANDOFF / COMPLETE / BLOCKED / CHECKPOINT;
  resume-on-no-marker; forced handoff turns; hang detection; crash-safe restarts (guard).
- Context enforcement: Claude Code hooks (warn / deny / stop gate); live usage + stop-at-hard-limit for agents
  without hooks; checkpoint mode; percentage and absolute-token thresholds per agent; learned context windows.
- Usage limits: structured reset times (Claude `rate_limit_event`, Codex rollout `rate_limits`) and prose parsing
  ("try again at 3:42 PM", "in 2h 13m"); fallback timer; cross-agent failover with switch-back.
- Adapters: Claude Code and Codex CLI (live-tested); Gemini CLI, Kiro, Qwen Code, Amp, Cursor CLI, opencode,
  Copilot CLI, aider (beta); custom CLIs from config.
- `relaykit scan` codebase profiling; relaykit-init / relaykit-plan / relaykit-run skills (Agent Skills format).
- Agent confirmation: model + reasoning effort confirmed per agent (including failover agents) before first use.
- Git checkpoints (commit in place, or own worktree + branch), notifications (desktop, ntfy, Discord/Slack/any
  webhook), budget caps (hours, sessions, runs, cost), keep-awake on Windows/macOS/Linux.
- Claude Code plugin + marketplace, Gemini CLI extension, `relaykit install` for every agent's skills folder.
