"""The relay protocol: markers and the progress file.

A session ends every turn with ONE marker on its last non-empty line:

    RELAY HANDOFF     progress is saved; start a fresh session
    RELAY COMPLETE    the plan is done and the progress file's Status says RELAY COMPLETE
    RELAY BLOCKED     impossible to continue without the owner (reason written under Blockers)
    RELAY CHECKPOINT  a step is done; the supervisor decides "continue" or "hand off now"
                      (used by agents that cannot be watched mid-turn; see docs/PROTOCOL.md)

The progress file (RELAY_PROGRESS.md) is the only state. Its "Current" table carries
``| Status | IN PROGRESS / BLOCKED / RELAY COMPLETE |`` plus Session, Phase, Step, Last updated.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

HANDOFF = "RELAY HANDOFF"
COMPLETE = "RELAY COMPLETE"
BLOCKED = "RELAY BLOCKED"
CHECKPOINT = "RELAY CHECKPOINT"
MARKERS = (HANDOFF, COMPLETE, BLOCKED, CHECKPOINT)

STATUS_IN_PROGRESS = "IN PROGRESS"
STATUS_BLOCKED = "BLOCKED"
STATUS_COMPLETE = "RELAY COMPLETE"

_DECOR = re.compile(r"^[\s>*_`#\-]+|[\s*_`.!]+$")


def last_line(text: str) -> str:
    if not text:
        return ""
    for ln in reversed(text.splitlines()):
        if ln.strip():
            return ln.strip()
    return ""


def marker_of(text: str) -> Optional[str]:
    """The marker on the last non-empty line of ``text`` (markdown decoration ignored), or None.

    Only the LAST line counts: a marker quoted in the middle of a reply never ends a session."""
    tail = _DECOR.sub("", last_line(text)).upper()
    for m in (COMPLETE, HANDOFF, BLOCKED, CHECKPOINT):
        if tail == m or tail.endswith(m):
            return m
    return None


def _read(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def field(progress: Path, name: str) -> str:
    """A ``| Name | value |`` row from the progress file ('' if absent)."""
    pat = re.compile(r"^\|\s*" + re.escape(name) + r"\s*\|\s*(.*?)\s*\|\s*$", re.M)
    m = pat.search(_read(progress))
    return m.group(1).strip() if m else ""


def status(progress: Path) -> str:
    return field(progress, "Status")


def is_complete(progress: Path) -> bool:
    return STATUS_COMPLETE in status(progress).upper()


def section(progress: Path, heading: str, max_chars: int = 2000) -> str:
    """The body of a ``## heading`` section (up to the next ``## ``), trimmed."""
    text = _read(progress)
    m = re.search(r"^##\s+" + re.escape(heading) + r"\s*$", text, re.M)
    if not m:
        return ""
    rest = text[m.end():]
    n = re.search(r"^##\s+", rest, re.M)
    body = (rest[: n.start()] if n else rest).strip()
    return body if len(body) <= max_chars else body[:max_chars].rstrip() + " ..."


def phases(progress: Path) -> list[dict]:
    """Rows of the ``## Phases`` table: [{"#": "1", "Phase": ..., "Status": ..., "Notes": ...}]."""
    body = section(progress, "Phases", max_chars=100_000)
    rows = [ln for ln in body.splitlines() if ln.strip().startswith("|")]
    if len(rows) < 3:
        return []
    head = [c.strip() for c in rows[0].strip().strip("|").split("|")]
    out = []
    for ln in rows[2:]:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        out.append({head[i]: cells[i] if i < len(cells) else "" for i in range(len(head))})
    return out


def summary(progress: Path) -> dict:
    return {
        "status": status(progress),
        "session": field(progress, "Session"),
        "phase": field(progress, "Phase"),
        "step": field(progress, "Step"),
        "last_updated": field(progress, "Last updated"),
    }
