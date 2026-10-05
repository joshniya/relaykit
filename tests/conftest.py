import json
import os
import stat
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FAKE = Path(__file__).resolve().parent / "fake_agent.py"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never read or write the real ~/.relaykit while testing."""
    monkeypatch.setenv("RELAYKIT_HOME", str(tmp_path / "rkhome"))
    for k in list(os.environ):
        if k.startswith("RELAYKIT_") and k != "RELAYKIT_HOME":
            monkeypatch.delenv(k, raising=False)
    yield


def make_fake_cli(dirpath: Path, name: str = "fakeagent") -> Path:
    """An executable that runs fake_agent.py (a .cmd on Windows, a shell script elsewhere)."""
    dirpath.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        p = dirpath / f"{name}.cmd"
        p.write_text(f'@echo off\r\n"{sys.executable}" "{FAKE}" %*\r\n', encoding="utf-8")
    else:
        p = dirpath / name
        p.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8")
        p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / ".relaykit").mkdir(parents=True)
    (root / ".relaykit" / "PROJECT.md").write_text("# profile\n", encoding="utf-8")
    return root


def write_scenario(path: Path, turns: list) -> Path:
    path.write_text(json.dumps(turns), encoding="utf-8")
    return path


def calls(scen: Path) -> list:
    p = Path(str(scen) + ".calls")
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
