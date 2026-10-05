# Configuration

Layers, later wins: built-in defaults → `~/.relaykit/config.toml` (or `$RELAYKIT_HOME`) →
`<project>/.relaykit/config.toml` → `<relay>/relay.toml` → command-line flags.

```toml
[relay]
agent = "claude"              # the agent that starts the relay
handoff_pct = 35.0            # ask for a handoff at this % of the context window
hard_pct = 45.0               # past this, only the handoff is allowed (hooks) / the run is stopped (no hooks)
handoff_tokens_max = 0        # absolute caps; 0 = none. Recommended for 1M-token windows (e.g. 300000)
hard_tokens_max = 0
early_handoff_pct = 20.0      # after finishing a phase, hand off if past this
enforcement = "auto"          # auto | hooks | checkpoints
kill_at_hard = true           # agents with live usage but no hooks: stop the run at the hard limit

max_hours = 0                 # budget caps; 0 = unlimited. Hours count from the relay's first run
max_sessions = 0
max_cost_usd = 0              # cost-equivalent, where the CLI reports it (Claude Code does)
max_runs = 400

max_resumes_per_session = 6   # turns without a marker before asking for a handoff
max_checkpoints_per_session = 60
blind_checkpoints_per_session = 6   # agents that report no usage: steps per session
max_stalled_sessions = 3      # handoffs in a row that didn't touch the progress file -> stop
max_blocked = 4               # RELAY BLOCKED in a row -> stop

hang_minutes = 45             # no output for this long -> kill the run and retry
retry_base_seconds = 90       # back-off for overloaded/5xx, doubling...
retry_max_seconds = 900       # ...up to this
limit_wait_minutes = 20       # usage limit with no reset time given
limit_margin_seconds = 90     # added to a known reset time
auth_wait_minutes = 15        # signed out: wait, retry
max_auth_waits = 32
blocked_wait_minutes = 15
gap_seconds = 8               # pause between runs
keep_awake = true
phase_zero = true             # `relaykit new` adds a Phase 0 (orientation + baseline)
final_review = true           # ...and a final review + report phase

[agents.claude]
model = "claude-opus-5-5"     # blank = the CLI's default
effort = "high"               # reasoning effort (claude: low|medium|high|xhigh|max)
extra_args = []               # appended to every run
context_window = 0            # 0 = learn it (Claude: measured up front if you agree)
handoff_pct = 0               # per-agent overrides (0 = use [relay])
hard_pct = 0
handoff_tokens_max = 0
hard_tokens_max = 0
binary = ""                   # path to the CLI if it isn't on PATH
env = { }                     # extra environment for this agent's runs
confirmed = true              # set when you confirm model + effort; relaykit asks until you do

[agents.codex]
model = "gpt-5.6-sol"
effort = "high"               # becomes -c model_reasoning_effort="high"
confirmed = true

[agents.mycli]                # any other CLI (see ADAPTERS.md)
adapter = "custom"
command = ["mycli", "--yes", "{prompt}"]
resume_command = ["mycli", "--yes", "--session", "{session}", "{prompt}"]
confirmed = true

[failover]
enabled = true
chain = ["claude", "codex"]   # order of preference; the [relay] agent is always first
switch_back = true            # return to the first agent at the next fresh session after its limit resets
on_auth_error = true          # also fail over when an agent is signed out

[git]
mode = "worktree"             # off | commit | worktree
branch = "relaykit/{relay}"
worktree_dir = ""             # default: <repo>/../<repo-name>.relaykit-<relay>
commit_on = ["handoff", "complete"]

[notify]
events = ["complete", "blocked", "failed", "limit", "failover", "budget", "auth"]   # also: started, stopped
desktop = true
ntfy_topic = "my-relays"      # push to your phone with the ntfy app
ntfy_server = "https://ntfy.sh"
webhook_url = ""              # Discord/Slack webhook URLs get their native format; others get JSON
```

## Confirming agents

relaykit never chooses a model or reasoning effort silently. An agent is usable once `confirmed = true`:
`relaykit init` / `relaykit agents` ask interactively (showing the models and effort levels the CLI supports, e.g.
from Codex's model cache), `relaykit agents codex --model X --effort Y` sets it non-interactively, and `--model` /
`--effort` on `relaykit run` count as confirmation for that run. `--accept-defaults` explicitly accepts each CLI's
own defaults. Every `relaykit run` prints the full setup and asks before starting (`--yes` skips only that).
