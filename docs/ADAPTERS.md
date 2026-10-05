# Agent adapters

An adapter (`src/relaykit/adapters/`) teaches relaykit to drive one agent CLI headlessly:

| Method | Job |
|---|---|
| `build(spec) -> Launch` | the command line (+ stdin, env) for a new session, a resume, or a forced handoff turn |
| `on_line(line, turn)` | parse one line of output: session id, usage, assistant text, tools, rate limits, errors |
| `poll(spec, turn)` | every ~2 s while running: side channels (Codex's rollout file, Gemini's hook-reported usage) |
| `finish(spec, turn, exit_code, stderr)` | after exit: final text fallbacks, exit-code meanings, limit detection |
| `default_window(model)` | context window when it hasn't been learned or configured |
| `caps` | `hooks`, `live_usage`, `resume`, `preset_session_id`, `cost`, `structured_limits`, `tested` |

The supervisor turns the `Turn` into an `Outcome` and the decision table in `supervisor.decide` does the rest, so
an adapter never needs to know about handoffs, failover or budgets.

## How each CLI is driven

| Agent | New session | Resume | Usage source | Final text | Notes |
|---|---|---|---|---|---|
| Claude Code | `claude -p <prompt> --session-id <uuid> --output-format stream-json --verbose --dangerously-skip-permissions --disallowedTools AskUserQuestion [--settings hooks.json]` | `--resume <id>` | `assistant.message.usage` (input + cache_creation + cache_read) | `result.result` | `rate_limit_event` gives `resetsAt`; `result.modelUsage.*.contextWindow` teaches the window |
| Codex CLI | `codex exec --json --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -o last.txt -C <dir> -` (prompt on stdin) | `codex exec <flags> resume <thread_id> -` | rollout `token_count`: `last_token_usage.total_tokens` / `model_context_window` | last `agent_message` item | rollout `rate_limits.*.resets_at`; Codex hooks can't be set per run (`-c`) — checkpoint mode |
| Gemini CLI | `gemini -p <prompt> -o stream-json --approval-mode=yolo --skip-trust` | `-r <session_id>` | AfterModel hook → `promptTokenCount` | assistant `message`s after the last `tool_use` | exit 41 = auth; install hooks with `relaykit install --gemini-hooks` |
| Kiro CLI | `kiro-cli chat --output-format stream-json --trust-all-tools <prompt>` | `--resume-id <id>` | `metadata.contextUsagePercentage` | `runFinished.finalText` | auto-summary changes the session id; the adapter follows it |
| Qwen Code | `qwen -p <prompt> --output-format stream-json --yolo` | `--resume <id>` | Claude-shaped `message.usage` | `result.result` | |
| Amp | `amp --dangerously-allow-all --stream-json -x <prompt>` | `amp threads continue <T-id> ...` | Claude-shaped | `result.result` | |
| Cursor CLI | `cursor-agent -p <prompt> --output-format stream-json --force --trust` | `--resume <chatId>` | `result.usage` | `result.result` | binary may be `cursor-agent` or `agent` |
| opencode | `opencode run --format json <prompt>` | `-s <id>` | `step_finish.part.tokens` | `text` parts | autocompact disabled for relay runs |
| Copilot CLI | `copilot -p <prompt> --allow-all-tools --no-ask-user -s --output-format json` | `--resume=<id>` | `session.usage_info` | `assistant.message` (final_answer) | |
| aider | `aider --message-file <f> --yes-always --no-auto-commits` | — (fresh each turn) | — | stdout | no sessions; checkpoint mode with fixed steps |

On Windows, npm-installed CLIs are `.cmd` shims; relaykit resolves them to `node <script>` so prompts are passed
intact (cmd.exe re-parses batch-file arguments).

## A custom CLI without writing code

```toml
[agents.mycli]
adapter = "custom"
command = ["mycli", "run", "--yes", "{prompt}"]
resume_command = ["mycli", "run", "--yes", "--session", "{session}", "{prompt}"]   # optional
prompt_via = "arg"            # or "stdin"
context_window = 200000
confirmed = true
```

Placeholders: `{prompt}`, `{session}`, `{workdir}`, `{model}`, `{effort}`. Output is read as text; the last
non-empty line must carry the marker. Print `SESSION_ID=<id>` (or a JSON line with `"session_id"`) to enable
resume. Custom CLIs run in checkpoint mode.

## Writing a new adapter

1. Subclass `Adapter` (or `ClaudeAdapter` if the stream is Claude-shaped) in `src/relaykit/adapters/`.
2. Register it in `adapters/__init__.py`.
3. Add `build` + parsing tests with recorded output in `tests/test_adapters_hooks.py`.
4. Live-test it with the toy relay in `examples/toy/` and set `caps.tested = True` in the same PR.

If the CLI has hooks with "add context after a tool", "deny before a tool" and "block the stop", add its event
names to `hooks.EVENT_MAP` and its install step to `installer.py` — that's what gives an agent mid-turn
enforcement.
