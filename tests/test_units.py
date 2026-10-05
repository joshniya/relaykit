import json
import time
from pathlib import Path

import pytest

from relaykit import config, discover, limits, protocol, relays, tomlio
from relaykit.tomlio import _MiniParser


# ---------------------------------------------------------------- tomlio
SAMPLE = '''
# comment
[relay]
agent = "claude"   # trailing comment
handoff_pct = 35.5
max_runs = 400
keep_awake = true
chain = ["a", 'b',
  "c"]

[agents.codex]
model = "gpt-5.5"
extra_args = []
env = { FOO = "bar", N = 2 }
"quoted key" = "x\\ty"
'''


def test_toml_fallback_parser_matches_stdlib():
    mine = _MiniParser(SAMPLE).parse()
    assert mine["relay"]["chain"] == ["a", "b", "c"]
    assert mine["agents"]["codex"]["env"] == {"FOO": "bar", "N": 2}
    assert mine["agents"]["codex"]["quoted key"] == "x\ty"
    assert mine == tomlio.loads(SAMPLE)


def test_toml_roundtrip():
    data = {"relay": {"agent": "codex", "max_hours": 2.5, "flag": False, "list": ["x", "y"]},
            "agents": {"claude": {"model": 'a"b\\c', "confirmed": True}}}
    text = tomlio.dumps(data, header_comment="hello")
    assert text.startswith("# hello")
    assert tomlio.loads(text) == data
    assert _MiniParser(text).parse() == data


# ---------------------------------------------------------------- protocol
@pytest.mark.parametrize("text,marker", [
    ("work done\nRELAY HANDOFF", protocol.HANDOFF),
    ("done\n\n**RELAY COMPLETE**\n", protocol.COMPLETE),
    ("x\n`RELAY BLOCKED`", protocol.BLOCKED),
    ("step saved. RELAY CHECKPOINT.", protocol.CHECKPOINT),
    ("I will print RELAY HANDOFF later\nstill working", None),
    ("", None),
])
def test_marker_only_on_last_line(text, marker):
    assert protocol.marker_of(text) == marker


def test_progress_fields_and_phases(tmp_path):
    p = tmp_path / "RELAY_PROGRESS.md"
    p.write_text("## Current\n| Field | Value |\n|---|---|\n| Status | IN PROGRESS |\n| Phase | Phase 1 — spine |\n"
                 "\n## Phases\n| # | Phase | Status | Notes |\n|---|---|---|---|\n| 0 | Orientation | done | ok |\n"
                 "| 1 | Spine | todo | |\n\n## Session log\n- x\n", encoding="utf-8")
    assert protocol.status(p) == "IN PROGRESS"
    assert not protocol.is_complete(p)
    rows = protocol.phases(p)
    assert [r["#"] for r in rows] == ["0", "1"] and rows[0]["Status"] == "done"
    p.write_text(p.read_text().replace("IN PROGRESS", "RELAY COMPLETE"), encoding="utf-8")
    assert protocol.is_complete(p)


# ---------------------------------------------------------------- limits
def test_parse_reset_relative():
    now = 1_000_000.0
    assert limits.parse_reset("Try again in 2h 13m", now) == now + 2 * 3600 + 13 * 60
    assert limits.parse_reset("rate limited; resets in 45 minutes", now) == now + 45 * 60
    assert limits.parse_reset("Suggested retry after 30s", now) == now + 30


def test_parse_reset_clock_time_is_future():
    now = time.time()
    t = limits.parse_reset("You’ve hit your usage limit. Try again at 3:42 PM.", now)
    assert t is not None and now < t <= now + 86400 + 60


def test_parse_reset_dated():
    t = limits.parse_reset("Try again at Oct 6th, 2026 3:42 PM.")
    assert time.localtime(t).tm_mon == 10 and time.localtime(t).tm_mday == 6 and time.localtime(t).tm_hour == 15


def test_classify():
    assert limits.classify("API Error: 529 overloaded")["busy"]
    assert limits.classify("Invalid API key · Please run /login")["auth"]
    assert limits.classify("You've hit your usage limit")["limit"]
    assert limits.classify("prompt is too long: 210000 tokens")["context"]
    assert not any(limits.classify("all tests passed").values())


# ---------------------------------------------------------------- config
def test_config_layers_and_chain(tmp_path, monkeypatch):
    root = tmp_path / "p"
    (root / ".relaykit").mkdir(parents=True)
    user = Path(str(tmp_path / "rkhome"))
    user.mkdir(parents=True, exist_ok=True)
    (user / "config.toml").write_text('[relay]\nhandoff_pct = 30\n[agents.claude]\nmodel = "u"\n', encoding="utf-8")
    (root / ".relaykit" / "config.toml").write_text(
        '[relay]\nagent = "codex"\n[failover]\nenabled = true\nchain = ["claude", "gemini"]\n'
        '[agents.claude]\neffort = "high"\n', encoding="utf-8")
    s = config.load(root, overrides={"relay": {"max_hours": 3}})
    assert s.handoff_pct == 30 and s.max_hours == 3 and s.raw["relay"]["agent"] == "codex"
    assert s.chain() == ["codex", "claude", "gemini"]
    assert s.agent("claude").model == "u" and s.agent("claude").effort == "high"
    a = s.agent("codex")
    a.model, a.effort, a.confirmed = "m", "xhigh", True
    config.set_agent(root / ".relaykit" / "config.toml", a)
    s2 = config.load(root)
    assert s2.agent("codex").confirmed and s2.agent("codex").effort == "xhigh"
    assert s2.chain() == ["codex", "claude", "gemini"]   # the rest of the file survived


# ---------------------------------------------------------------- relays
PLAN = """# Big thing
## Progress at a glance
- [ ] **Phase 1 — Spine**
- [ ] **Phase 2 — Money {and} stuff**
## Goal
x
"""


def test_create_relay_from_plan(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN, encoding="utf-8")
    d = relays.create(root, "Big Thing", plan_src=plan)
    assert d.name == "big-thing"
    prog = (d / "RELAY_PROGRESS.md").read_text(encoding="utf-8")
    prompt = (d / "RELAY_PROMPT.md").read_text(encoding="utf-8")
    assert "{{" not in prog and "{{" not in prompt
    rows = protocol.phases(d / "RELAY_PROGRESS.md")
    assert [r["#"] for r in rows] == ["0", "1", "2", "R"]
    assert rows[2]["Phase"] == "Money {and} stuff"
    assert ".relaykit/relays/big-thing/RELAY_PROGRESS.md" in prompt
    assert protocol.status(d / "RELAY_PROGRESS.md") == "IN PROGRESS"
    with pytest.raises(FileExistsError):
        relays.create(root, "big thing", plan_src=plan)


def test_plan_phases_from_headings():
    assert relays.plan_phases("### Phase 1 — A\n### Phase 2: B\n") == [("Phase 1", "A"), ("Phase 2", "B")]


# ---------------------------------------------------------------- discover
def test_scan_node_and_python(tmp_path):
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({"scripts": {"test": "vitest", "build": "vite build", "lint": "eslint ."}}))
    (root / "pnpm-lock.yaml").write_text("")
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n[tool.ruff]\n")
    (root / "Makefile").write_text("test:\n\techo\nbuild: deps\n\techo\n")
    (root / "AGENTS.md").write_text("rules")
    (root / "a.py").write_bytes(b"x = 1\r\n")
    (root / "b.ts").write_bytes(b"let x = 1\n")
    info = discover.scan(root)
    cmds = {(c["purpose"], c["command"]) for c in info["commands"]}
    assert ("install", "pnpm install") in cmds and ("test", "pnpm run test") in cmds
    assert ("test", "python -m pytest") in cmds and ("lint", "ruff check .") in cmds
    assert ("test", "make test") in cmds and ("build", "make build") in cmds
    assert info["line_endings"] == "mixed" and "AGENTS.md" in info["instruction_files"]
    md = discover.render_profile(info, previous="old\n<!-- relaykit:keep -->\n## Mine\nkeep me\n")
    assert "keep me" in md and "Mixed line endings" in md
