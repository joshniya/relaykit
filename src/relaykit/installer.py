"""Installing relaykit's skills (and Gemini's hooks) into each agent's own folders.

Skills follow the open Agent Skills format (SKILL.md folders). ``.agents/skills`` is read by
Codex, Gemini CLI, opencode, Cursor, Copilot and Amp; Claude Code reads ``.claude/skills`` (or
use the relaykit plugin); Kiro and Qwen have their own folders."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import procs

ASSETS = Path(__file__).resolve().parent / "assets"
SKILLS = ASSETS / "skills"

# agent -> (project dir, user dir)
SKILL_DIRS = {
    "agents": (".agents/skills", "~/.agents/skills"),     # codex, gemini, opencode, cursor, copilot, amp
    "claude": (".claude/skills", "~/.claude/skills"),
    "kiro": (".kiro/skills", "~/.kiro/skills"),
    "qwen": (".qwen/skills", "~/.qwen/skills"),
}


def skill_names() -> list:
    return sorted(p.name for p in SKILLS.iterdir() if (p / "SKILL.md").exists()) if SKILLS.is_dir() else []


def install_skills(targets: list, scope: str, root: Path) -> list:
    done = []
    for t in targets:
        proj, user = SKILL_DIRS[t]
        base = (Path(root) / proj) if scope == "project" else Path(user).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        for name in skill_names():
            dst = base / name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(SKILLS / name, dst)
            done.append(str(dst))
    return done


def hook_command(agent: str) -> str:
    hook = (Path(__file__).resolve().parent / "_hook.py").as_posix()
    return f'"{procs.python_exe().replace(chr(92), "/")}" "{hook}" {agent}'


def install_gemini_hooks(root: Path, scope: str = "project") -> Path:
    """Add relaykit's hooks to Gemini's settings.json. They do nothing outside a relay run
    (RELAYKIT_ACTIVE unset), so ordinary Gemini sessions are unaffected."""
    path = (Path(root) / ".gemini" / "settings.json") if scope == "project" else Path("~/.gemini/settings.json").expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        raise RuntimeError(f"{path} isn't valid JSON; fix it first")
    cmd = hook_command("gemini")
    entry = {"matcher": "*", "hooks": [{"type": "command", "command": cmd, "timeout": 30000, "name": "relaykit"}]}
    hooks = data.setdefault("hooks", {})
    for ev in ("AfterTool", "BeforeTool", "AfterAgent", "AfterModel"):
        lst = [h for h in hooks.get(ev, []) if "relaykit" not in json.dumps(h)]
        lst.append(entry)
        hooks[ev] = lst
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def uninstall_gemini_hooks(root: Path, scope: str = "project") -> bool:
    path = (Path(root) / ".gemini" / "settings.json") if scope == "project" else Path("~/.gemini/settings.json").expanduser()
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    hooks = data.get("hooks") or {}
    changed = False
    for ev in list(hooks):
        kept = [h for h in hooks[ev] if "relaykit" not in json.dumps(h)]
        if len(kept) != len(hooks[ev]):
            changed = True
            hooks[ev] = kept
    if changed:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return changed


GITIGNORE_LINES = [".relaykit/logs/"]


def ensure_gitignore(root: Path) -> bool:
    gi = Path(root) / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    missing = [ln for ln in GITIGNORE_LINES if ln not in text.splitlines()]
    if not missing:
        return False
    with open(gi, "a", encoding="utf-8") as fh:
        if text and not text.endswith("\n"):
            fh.write("\n")
        fh.write("# relaykit run logs\n" + "\n".join(missing) + "\n")
    return True
