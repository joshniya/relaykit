"""Supervisor state (``.relaykit/logs/<relay>/state.json``): what is running now, totals for the
budget caps, which agents are rate-limited until when, and learned context-window sizes.

The PROGRESS FILE is the relay's real state; this file only lets ``status``/``watch``/``stop``
see the supervisor and lets a restarted supervisor keep its budget totals."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

STOP_FILE = "STOP"
DONE_FILE = "RELAY_DONE"


class State:
    def __init__(self, logs: Path):
        self.logs = Path(logs)
        self.path = self.logs / "state.json"
        self.data: dict = {}
        self.load()

    def load(self) -> None:
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}
        self.data.setdefault("totals", {"runs": 0, "sessions": 0, "cost_usd": 0.0, "seconds": 0.0})
        self.data.setdefault("limited_until", {})
        self.data.setdefault("windows", {})

    def save(self) -> None:
        self.data["updated_at"] = time.time()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, **kw: Any) -> None:
        self.data.update(kw)
        self.save()

    # ---- control files
    def stop_requested(self) -> bool:
        return (self.logs / STOP_FILE).exists()

    def request_stop(self, now: bool = False) -> None:
        (self.logs / STOP_FILE).write_text("now" if now else "after-run", encoding="utf-8")

    def stop_mode(self) -> str:
        try:
            return (self.logs / STOP_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def clear_stop(self) -> None:
        try:
            (self.logs / STOP_FILE).unlink()
        except OSError:
            pass

    def mark_done(self) -> None:
        (self.logs / DONE_FILE).write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")

    def is_done(self) -> bool:
        return (self.logs / DONE_FILE).exists()

    def clear_done(self) -> None:
        try:
            (self.logs / DONE_FILE).unlink()
        except OSError:
            pass
