"""Git checkpoints.

Modes (``[git] mode``):
  off       relaykit never runs a git write (the default).
  commit    commit everything at each checkpoint, in place, on the current branch. Only safe when
            nobody else edits this working tree while the relay runs.
  worktree  the relay works in its own git worktree on branch ``relaykit/<relay>`` and commits
            there at each checkpoint. Your working tree is never touched; merge the branch when
            you're happy. Recommended.

relaykit never pushes, never rewrites history and never skips your git hooks.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional


def git(cwd: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=check)


def available() -> bool:
    return shutil.which("git") is not None


def is_repo(path: Path) -> bool:
    return available() and git(path, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def toplevel(path: Path) -> Optional[Path]:
    r = git(path, "rev-parse", "--show-toplevel")
    return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def branch(path: Path) -> str:
    return git(path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def head(path: Path) -> str:
    return git(path, "rev-parse", "--short", "HEAD").stdout.strip()


def dirty_files(path: Path) -> list:
    out = git(path, "status", "--porcelain").stdout
    return [ln[3:] for ln in out.splitlines() if ln.strip()]


def is_tracked(repo: Path, rel_path: str) -> bool:
    return bool(git(repo, "ls-files", "--", rel_path).stdout.strip())


def default_worktree_dir(repo: Path, relay: str) -> Path:
    return repo.parent / f"{repo.name}.relaykit-{relay}"


def ensure_worktree(repo: Path, relay: str, branch_tpl: str, wt_dir: str = "") -> tuple:
    """Create (or reuse) the relay's worktree. Returns (worktree_path, branch_name, created)."""
    br = branch_tpl.replace("{relay}", relay)
    path = Path(wt_dir).expanduser() if wt_dir else default_worktree_dir(repo, relay)
    if (path / ".git").exists():
        return path, br, False
    exists = git(repo, "rev-parse", "--verify", "--quiet", br).returncode == 0
    args = ["worktree", "add", str(path), br] if exists else ["worktree", "add", "-b", br, str(path)]
    r = git(repo, *args)
    if r.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {r.stderr.strip() or r.stdout.strip()}")
    return path, br, True


def copy_untracked_into(repo: Path, worktree: Path, rel_paths: list) -> list:
    """Files the relay needs (its folder, PROJECT.md, config) that aren't committed yet don't exist
    in a fresh worktree; copy them across so the first session can find them."""
    copied = []
    for rp in rel_paths:
        src = repo / rp
        dst = worktree / rp
        if not src.exists() or dst.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        copied.append(rp)
    return copied


def commit_all(path: Path, message: str) -> tuple:
    """Stage everything and commit. Returns (ok, detail). An empty change set is ok=True."""
    if not dirty_files(path):
        return True, "nothing to commit"
    r = git(path, "add", "-A")
    if r.returncode != 0:
        return False, r.stderr.strip()
    r = git(path, "commit", "-m", message)
    if r.returncode != 0:
        return False, (r.stderr.strip() or r.stdout.strip())[:500]
    return True, head(path)
