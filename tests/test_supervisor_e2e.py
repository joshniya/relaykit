"""End-to-end: the real supervisor driving a scripted fake agent CLI (Claude-shaped stream)."""
import time


from conftest import calls, make_fake_cli, write_scenario
from relaykit import config, protocol, relays
from relaykit.state import State
from relaykit.supervisor import EXIT_BLOCKED, EXIT_BUDGET, EXIT_DONE, Supervisor

FAST = {"gap_seconds": 0, "retry_base_seconds": 0.2, "retry_max_seconds": 0.5, "blocked_wait_minutes": 0.002,
        "limit_wait_minutes": 0.002, "limit_margin_seconds": 0, "auth_wait_minutes": 0.002, "keep_awake": False,
        "hang_minutes": 0.05}


def setup(project, tmp_path, turns, agents=None, relay_over=None, failover=None):
    fake = make_fake_cli(tmp_path / "bin")
    scen = write_scenario(tmp_path / "scenario.json", turns)
    d = relays.create(project, "demo", goal="demo", phase_zero=False, final_review=False)
    agents = agents or {"claude": {"binary": str(fake), "confirmed": True, "env": {"FAKE_SCENARIO": str(scen)}}}
    for a in agents.values():
        a.setdefault("binary", str(fake))
        a.setdefault("confirmed", True)
        a.setdefault("env", {"FAKE_SCENARIO": str(scen)})
    over = {"relay": {**FAST, **(relay_over or {})}, "agents": agents}
    if failover:
        over["failover"] = failover
    s = config.load(project, d, over)
    return Supervisor(project, d, s, echo=False), d, scen


def test_handoff_then_complete(project, tmp_path):
    sup, d, scen = setup(project, tmp_path, [
        {"text": "did phase 1\nRELAY HANDOFF", "touch_progress": True, "ctx": 30000},
        {"text": "all done\nRELAY COMPLETE", "complete": True},
    ])
    assert sup.run() == EXIT_DONE
    cs = calls(scen)
    assert len(cs) == 2
    assert "--session-id" in cs[0]["argv"] and "--session-id" in cs[1]["argv"]          # two fresh sessions
    assert cs[0]["env_active"] == "1"
    assert protocol.is_complete(d / "RELAY_PROGRESS.md")
    st = State(sup.logs)
    assert st.is_done() and st.data["totals"]["sessions"] == 2


def test_no_marker_resumes_same_session(project, tmp_path):
    sup, d, scen = setup(project, tmp_path, [
        {"text": "working..."},
        {"text": "saved\nRELAY HANDOFF", "touch_progress": True},
        {"text": "RELAY COMPLETE", "complete": True},
    ])
    assert sup.run() == EXIT_DONE
    cs = calls(scen)
    assert "--resume" in cs[1]["argv"]
    sid0 = cs[0]["argv"][cs[0]["argv"].index("--session-id") + 1]
    assert cs[1]["argv"][cs[1]["argv"].index("--resume") + 1] == sid0
    assert "--session-id" in cs[2]["argv"]


def test_rate_limit_waits_then_resumes(project, tmp_path):
    reset = int(time.time()) + 2
    sup, d, scen = setup(project, tmp_path, [
        {"text": "", "rate_limit": reset},
        {"text": "RELAY COMPLETE", "complete": True},
    ])
    t0 = time.time()
    assert sup.run() == EXIT_DONE
    assert time.time() - t0 >= 1.5          # it actually waited for the reset
    cs = calls(scen)
    assert "--resume" in cs[1]["argv"]      # same session after the wait


def test_failover_to_second_agent(project, tmp_path):
    far = int(time.time()) + 3600
    fake = make_fake_cli(tmp_path / "bin")
    scen_a = write_scenario(tmp_path / "a.json", [{"text": "", "rate_limit": far}])
    scen_b = write_scenario(tmp_path / "b.json", [{"text": "took over\nRELAY COMPLETE", "complete": True}])
    agents = {"alpha": {"adapter": "claude", "binary": str(fake), "env": {"FAKE_SCENARIO": str(scen_a)}},
              "beta": {"adapter": "claude", "binary": str(fake), "env": {"FAKE_SCENARIO": str(scen_b)}}}
    sup, d, _ = setup(project, tmp_path, [{}], agents=agents, relay_over={"agent": "alpha"},
                      failover={"enabled": True, "chain": ["alpha", "beta"]})
    assert sup.run() == EXIT_DONE
    assert len(calls(scen_a)) == 1 and len(calls(scen_b)) == 1
    assert "interrupted" in calls(scen_b)[0]["argv"][calls(scen_b)[0]["argv"].index("-p") + 1] or \
           "interrupted" in calls(scen_b)[0]["stdin"]
    assert State(sup.logs).data["limited_until"]["alpha"] >= far


def test_blocked_repeatedly_stops(project, tmp_path):
    sup, d, scen = setup(project, tmp_path, [{"text": "need a key\nRELAY BLOCKED"}], relay_over={"max_blocked": 2})
    assert sup.run() == EXIT_BLOCKED
    assert len(calls(scen)) == 2


def test_stalled_handoffs_stop(project, tmp_path):
    sup, d, scen = setup(project, tmp_path, [{"text": "RELAY HANDOFF"}], relay_over={"max_stalled_sessions": 2})
    assert sup.run() == EXIT_BLOCKED
    assert State(sup.logs).data["status"] == "stalled"


def test_budget_sessions(project, tmp_path):
    sup, d, scen = setup(project, tmp_path, [{"text": "RELAY HANDOFF", "touch_progress": True}],
                         relay_over={"max_sessions": 2})
    assert sup.run() == EXIT_BUDGET
    assert len(calls(scen)) == 2


def test_hung_run_is_killed_and_retried(project, tmp_path):
    sup, d, scen = setup(project, tmp_path, [
        {"silent_sleep": 30},
        {"text": "RELAY COMPLETE", "complete": True},
    ])
    t0 = time.time()
    assert sup.run() == EXIT_DONE
    assert time.time() - t0 < 25
    assert len(calls(scen)) == 2


def test_stop_file_stops_between_runs(project, tmp_path):
    sup, d, scen = setup(project, tmp_path, [{"text": "RELAY HANDOFF", "touch_progress": True}])
    State(sup.logs).request_stop()
    sup.state.clear_stop = lambda: None     # keep the stop request across start-up
    assert sup.run() == 2
    assert calls(scen) == []
