"""Cross-platform process plumbing: find executables, spawn an agent run with its output
streamed line by line, kill a whole process tree, and give the child a clean environment."""
from __future__ import annotations

import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

IS_WINDOWS = os.name == "nt"

# Variables that make an agent CLI think it is nested inside another agent session (an IDE agent,
# another claude/codex run). A relay session must look like a plain terminal run, so these go.
# API keys and normal configuration are NOT touched.
_NESTING_VARS = (
    "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT", "CLAUDE_PID", "CLAUDE_AGENT_SDK",
    "CLAUDE_CODE_SESSION", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR", "CLAUDE_EFFORT",
    "CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_COMPANION",
    "GEMINI_CLI", "GEMINI_SESSION_ID", "KIRO_SESSION_ID", "MCP_CONNECTION_NONBLOCKING",
)
_NESTING_PREFIXES = ("CLAUDE_CODE_SESSION", "CLAUDE_CODE_SUBAGENT", "CLAUDE_CODE_TEAM")


def clean_env(extra: Optional[dict] = None, base: Optional[dict] = None) -> dict:
    env = dict(base if base is not None else os.environ)
    for k in list(env):
        if k in _NESTING_VARS or k.startswith(_NESTING_PREFIXES) or k.startswith("RELAYKIT_"):
            env.pop(k, None)
    for k, v in (extra or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = str(v)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def which(name: str) -> Optional[str]:
    if not name:
        return None
    p = Path(name).expanduser()
    if p.is_file():
        return str(p)
    return shutil.which(name)


def is_batch(exe: str) -> bool:
    """npm shims on Windows (codex.cmd, gemini.cmd): never put free text in their argv —
    cmd.exe re-parses it — so prompts go through stdin for these."""
    return IS_WINDOWS and exe.lower().endswith((".cmd", ".bat"))


class Run:
    """A spawned agent run. Output lines arrive on ``lines`` (a queue of str; None = stream closed)."""

    def __init__(self, argv: list, cwd: Path, env: dict, stdin_text: Optional[str],
                 out_file: Path, err_file: Path):
        self.argv = argv
        self.out_file = Path(out_file)
        self.err_file = Path(err_file)
        self.lines: "queue.Queue[Optional[str]]" = queue.Queue()
        kwargs: dict = dict(cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL)
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(argv, **kwargs)
        self._threads = [
            threading.Thread(target=self._pump_out, daemon=True),
            threading.Thread(target=self._pump_err, daemon=True),
        ]
        for t in self._threads:
            t.start()
        if stdin_text is not None:
            try:
                self.proc.stdin.write(stdin_text.encode("utf-8"))
                self.proc.stdin.close()
            except OSError:
                pass

    @property
    def pid(self) -> int:
        return self.proc.pid

    def _pump_out(self) -> None:
        with open(self.out_file, "ab") as fh:
            for raw in iter(self.proc.stdout.readline, b""):
                fh.write(raw)
                fh.flush()
                self.lines.put(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        self.lines.put(None)

    def _pump_err(self) -> None:
        with open(self.err_file, "ab") as fh:
            for raw in iter(self.proc.stderr.readline, b""):
                fh.write(raw)
                fh.flush()

    def poll(self) -> Optional[int]:
        return self.proc.poll()

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        try:
            code = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        for t in self._threads:
            t.join(timeout=5)
        return code

    def kill_tree(self) -> None:
        kill_tree(self.proc.pid)
        try:
            self.proc.wait(timeout=15)
        except Exception:
            pass

    def stderr_text(self, limit: int = 20000) -> str:
        try:
            data = self.err_file.read_bytes()
        except OSError:
            return ""
        return data[-limit:].decode("utf-8", errors="replace")


def kill_tree(pid: int) -> None:
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        pass
    try:
        import time
        time.sleep(3)
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
            return str(pid) in out.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def detach(argv: list, cwd: Path, env: dict, log_file: Path, new_window: bool = False) -> int:
    """Start a long-lived background process that survives this terminal. Returns its pid."""
    out = open(log_file, "ab")
    kwargs: dict = dict(cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=out)
    if IS_WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        flags |= subprocess.CREATE_NEW_CONSOLE if new_window else (0x00000008 | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if new_window:
            kwargs.pop("stdout")
            kwargs.pop("stderr")
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    p = subprocess.Popen(argv, **kwargs)
    return p.pid


def python_exe() -> str:
    return sys.executable or "python3"
