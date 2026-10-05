import time

from relaykit import config, protocol
from relaykit.supervisor import Ctx, Outcome, decide, EXIT_BLOCKED


def S(**relay):
    return config.load(__import__("pathlib").Path("."), overrides={"relay": relay})


def test_complete_needs_status():
    s = S()
    assert decide(Outcome(marker=protocol.COMPLETE, complete_status=True), Ctx(), s).kind == "done"
    assert decide(Outcome(marker=protocol.COMPLETE, complete_status=False), Ctx(), s).kind == "resume"


def test_handoff_and_blocked():
    s = S(max_blocked=3)
    assert decide(Outcome(marker=protocol.HANDOFF), Ctx(), s).kind == "new"
    a = decide(Outcome(marker=protocol.BLOCKED), Ctx(blocked_streak=0), s)
    assert a.kind == "wait" and a.then == "new" and not a.count_run
    a = decide(Outcome(marker=protocol.BLOCKED), Ctx(blocked_streak=2), s)
    assert a.kind == "stop" and a.exit_code == EXIT_BLOCKED


def test_checkpoint_continue_or_handoff():
    s = S(blind_checkpoints_per_session=3)
    c = Ctx(handoff_tokens=35000)
    assert decide(Outcome(marker=protocol.CHECKPOINT, ctx_tokens=10000, usage_known=True), c, s).kind == "resume"
    assert decide(Outcome(marker=protocol.CHECKPOINT, ctx_tokens=36000, usage_known=True), c, s).kind == "handoff"
    # no usage at all: hand off after N steps
    assert decide(Outcome(marker=protocol.CHECKPOINT), Ctx(handoff_tokens=35000, checkpoints=2), s).kind == "handoff"


def test_rate_limit_waits_for_reset_or_fails_over():
    s = S(limit_margin_seconds=90)
    reset = time.time() + 600
    a = decide(Outcome(rate_limited=True, reset_at=reset), Ctx(), s)
    assert a.kind == "wait" and 600 < a.wait_seconds <= 691 and a.then == "resume"
    a = decide(Outcome(rate_limited=True), Ctx(), S(limit_wait_minutes=20))
    assert a.kind == "wait" and a.wait_seconds == 1200
    assert decide(Outcome(rate_limited=True, reset_at=reset), Ctx(failover_available=True), s).kind == "failover"


def test_failures_and_no_marker():
    s = S(max_resumes_per_session=2)
    assert decide(Outcome(context_overflow=True, failed=True), Ctx(), s).kind == "new"
    a = decide(Outcome(failed=True, busy=True, tail="529 overloaded"), Ctx(retry_wait=90), s)
    assert a.kind == "wait" and a.wait_seconds == 90
    assert decide(Outcome(no_result=True, seconds=3), Ctx(mode0="resume"), s).kind == "new"
    assert decide(Outcome(), Ctx(resumes=0), s).kind == "resume"
    assert decide(Outcome(), Ctx(resumes=2), s).kind == "handoff"
    assert decide(Outcome(), Ctx(mode0="handoff"), s).kind == "new"
    assert decide(Outcome(killed_for_context=True, ctx_pct=46), Ctx(), s).kind == "handoff"


def test_auth():
    s = S(max_auth_waits=2)
    assert decide(Outcome(auth_error=True, failed=True), Ctx(), s).kind == "wait"
    assert decide(Outcome(auth_error=True, failed=True), Ctx(auth_waits=2), s).kind == "stop"
    assert decide(Outcome(auth_error=True, failed=True), Ctx(failover_available=True), s).kind == "failover"
