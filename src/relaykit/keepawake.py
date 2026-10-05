"""Keep the machine from sleeping while a relay runs (released when the relay stops).

Windows: SetThreadExecutionState. macOS: ``caffeinate -i -w <pid>``. Linux: ``systemd-inhibit``
when available. Everything is best-effort: a missing tool never stops a relay."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys


class KeepAwake:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.how = "off"
        self._proc = None

    def __enter__(self) -> "KeepAwake":
        if not self.enabled:
            return self
        try:
            if os.name == "nt":
                import ctypes
                ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
                self.how = "SetThreadExecutionState"
            elif sys.platform == "darwin" and shutil.which("caffeinate"):
                self._proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
                self.how = "caffeinate"
            elif shutil.which("systemd-inhibit"):
                self._proc = subprocess.Popen(
                    ["systemd-inhibit", "--what=idle:sleep", "--who=relaykit", "--why=relay running",
                     "--mode=block", "sleep", "infinity"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.how = "systemd-inhibit"
        except Exception:
            self.how = "unavailable"
        return self

    def __exit__(self, *exc) -> None:
        try:
            if os.name == "nt" and self.how == "SetThreadExecutionState":
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            if self._proc is not None:
                self._proc.terminate()
        except Exception:
            pass
