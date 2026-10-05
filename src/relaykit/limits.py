"""Reading failure text: usage limits (and when they reset), overload, sign-in problems, and
"the conversation is too long". Adapters report structured signals when their CLI gives them;
these patterns are the fallback for CLIs that only print a sentence."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Optional

BUSY = re.compile(
    r"(?i)overloaded|at capacity|high demand|try again later|too many requests|service unavailable|"
    r"internal server error|\b(500|502|503|504|529)\b|temporarily unavailable|connection (reset|refused|closed|error)|"
    r"network error|ECONNRESET|ETIMEDOUT|socket hang up|fetch failed|stream disconnected")
AUTH = re.compile(
    r"(?i)invalid api key|not logged in|please (run )?/?login|run `?\w+ login|authentication_error|unauthori[sz]ed|"
    r"oauth token (has )?(expired|revoked)|\b401\b|credit balance is too low|account (is )?(disabled|suspended)|"
    r"sign in again|session expired|login required")
LIMIT = re.compile(
    r"(?i)usage limit|hit your (usage )?limit|limit reached|rate_limit|rate limit exceeded|\b429\b|quota exceeded|"
    r"out of credits|credits depleted|resets? (at|in) |try again (at|in) ")
CONTEXT = re.compile(
    r"(?i)prompt is too long|context (window|length) (exceeded|limit)|maximum context|too many tokens|"
    r"conversation is too long|ran out of room in the model's context window|context_window_exceeded")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _clock(h: str, m: Optional[str], ampm: Optional[str]) -> tuple:
    hh = int(h)
    mm = int(m) if m else 0
    if ampm:
        ampm = ampm.lower().replace(".", "")
        if ampm.startswith("p") and hh < 12:
            hh += 12
        if ampm.startswith("a") and hh == 12:
            hh = 0
    return hh, mm


def parse_reset(text: str, now: Optional[float] = None) -> Optional[float]:
    """Best-effort epoch seconds of a reset time written in prose. None when nothing parses.

    Handles: "try again in 2h 13m", "resets in 45 minutes", "in 30s", "try again at 3:42 PM",
    "resets 5pm", "Oct 6th, 2026 3:42 PM", and ISO timestamps. Clock times are LOCAL; a clock time
    that has already passed today is taken as tomorrow."""
    if not text:
        return None
    now = time.time() if now is None else now
    t = text.replace("’", "'")

    m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)", t)
    if m:
        iso = m.group(1).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                return dt.timestamp()
            return dt.timestamp()
        except ValueError:
            pass

    m = re.search(r"(?i)\b(?:in|after)\s+((?:\d+\s*(?:d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes|s|sec|secs|second|seconds)\b[\s,and]*)+)", t)
    if m:
        total = 0
        for num, unit in re.findall(r"(\d+)\s*([a-z]+)", m.group(1).lower()):
            n = int(num)
            if unit.startswith("d"):
                total += n * 86400
            elif unit.startswith("h"):
                total += n * 3600
            elif unit.startswith("m"):
                total += n * 60
            elif unit.startswith("s"):
                total += n
        if total:
            return now + total

    m = re.search(r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?\s*(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?", t)
    if m:
        mon = _MONTHS[m.group(1).lower()]
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else datetime.fromtimestamp(now).year
        hh, mm = _clock(m.group(4), m.group(5), m.group(6))
        try:
            return datetime(year, mon, day, hh, mm).timestamp()
        except ValueError:
            pass

    m = re.search(r"(?i)\b(?:at|resets?|until)\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?!\d)", t)
    if m and (m.group(2) or m.group(3)):
        hh, mm = _clock(m.group(1), m.group(2), m.group(3))
        base = datetime.fromtimestamp(now)
        cand = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand.timestamp() <= now:
            cand = cand + timedelta(days=1)
        return cand.timestamp()
    return None


def classify(text: str) -> dict:
    t = text or ""
    return {
        "busy": bool(BUSY.search(t)),
        "auth": bool(AUTH.search(t)),
        "limit": bool(LIMIT.search(t)),
        "context": bool(CONTEXT.search(t)),
    }
