"""A scriptable stand-in for an agent CLI, used by the end-to-end tests.

Each invocation plays the next turn from the JSON scenario in $FAKE_SCENARIO (a list of turns);
the turn counter lives in $FAKE_SCENARIO + ".n". A turn looks like:

  {"text": "did a step\\nRELAY HANDOFF",   # final assistant text (marker on the last line)
   "ctx": 12000,                          # context tokens to report
   "touch_progress": true,                # append to the progress file (counts as progress)
   "complete": false,                     # set the progress file's Status to RELAY COMPLETE
   "rate_limit": 1700000000,              # emit a rejected rate_limit_event with this resetsAt
   "error": "overloaded",                 # make the result an error with this text
   "exit": 0, "sleep": 0, "silent_sleep": 0}

Output is Claude-Code-shaped stream-json. Every invocation's argv + stdin is appended to
$FAKE_SCENARIO + ".calls" so tests can assert on how the supervisor drove it.
"""
import json
import os
import sys
import time
import uuid


def main() -> int:
    scen = os.environ["FAKE_SCENARIO"]
    turns = json.load(open(scen, encoding="utf-8"))
    counter = scen + ".n"
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    with open(counter, "w") as fh:
        fh.write(str(n + 1))
    stdin = ""
    if not sys.stdin.isatty():
        try:
            stdin = sys.stdin.read()
        except Exception:
            stdin = ""
    with open(scen + ".calls", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"n": n, "argv": sys.argv[1:], "stdin": stdin,
                             "env_active": os.environ.get("RELAYKIT_ACTIVE"),
                             "env_handoff": os.environ.get("RELAYKIT_HANDOFF_TOKENS")}) + "\n")
    t = turns[min(n, len(turns) - 1)]
    sid = None
    argv = sys.argv[1:]
    for flag in ("--session-id", "--resume"):
        if flag in argv:
            sid = argv[argv.index(flag) + 1]
    sid = sid or str(uuid.uuid4())

    def out(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    if t.get("silent_sleep"):
        time.sleep(t["silent_sleep"])
    out({"type": "system", "subtype": "init", "session_id": sid, "model": "fake-model", "tools": []})
    prog = os.environ.get("RELAYKIT_RELAY_DIR")
    if prog and t.get("touch_progress"):
        p = os.path.join(prog, "RELAY_PROGRESS.md")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"\n- fake turn {n}\n")
    if prog and t.get("complete"):
        p = os.path.join(prog, "RELAY_PROGRESS.md")
        s = open(p, encoding="utf-8").read().replace("| Status | IN PROGRESS |", "| Status | RELAY COMPLETE |")
        open(p, "w", encoding="utf-8").write(s)
    ctx = int(t.get("ctx", 1000))
    out({"type": "assistant", "parent_tool_use_id": None, "message": {
        "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "echo hi"}}],
        "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": ctx - 10}}})
    if t.get("sleep"):
        time.sleep(t["sleep"])
    if t.get("rate_limit"):
        out({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": t["rate_limit"],
                                                             "rateLimitType": "five_hour"}})
    text = t.get("text", "")
    out({"type": "assistant", "parent_tool_use_id": None, "message": {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": ctx - 10}}})
    err = t.get("error")
    out({"type": "result", "subtype": "error" if err else "success", "is_error": bool(err or t.get("rate_limit")),
         "result": err or text, "total_cost_usd": t.get("cost", 0.01),
         "modelUsage": {"fake-model": {"contextWindow": t.get("window", 100000), "inputTokens": 10,
                                       "cacheReadInputTokens": ctx, "cacheCreationInputTokens": 0}}})
    return int(t.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
