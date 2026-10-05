"""Where relaykit keeps things inside a project.

    <project>/.relaykit/config.toml          project settings (agents, limits, notifications, git)
    <project>/.relaykit/PROJECT.md           the codebase profile every session reads ("house rules")
    <project>/.relaykit/project.json         the machine-readable scan behind PROJECT.md
    <project>/.relaykit/relays/<name>/       one folder per relay: PLAN.md, RELAY_PROMPT.md,
                                             RELAY_PROGRESS.md, optional relay.toml
    <project>/.relaykit/logs/<name>/         run logs and supervisor state (gitignored)

A relay folder can also live anywhere else (e.g. ``plans/my-relay/``) as long as it holds
RELAY_PROMPT.md and RELAY_PROGRESS.md; its logs still go under ``.relaykit/logs/<folder name>``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

RK_DIR = ".relaykit"
PROMPT_FILE = "RELAY_PROMPT.md"
PROGRESS_FILE = "RELAY_PROGRESS.md"
PLAN_FILE = "PLAN.md"
RELAY_TOML = "relay.toml"
PROJECT_MD = "PROJECT.md"
PROJECT_JSON = "project.json"
CONFIG_TOML = "config.toml"


def user_config_dir() -> Path:
    """Per-user settings: ~/.relaykit (or $RELAYKIT_HOME)."""
    env = os.environ.get("RELAYKIT_HOME")
    return Path(env).expanduser() if env else Path.home() / ".relaykit"


def find_project_root(start: Optional[os.PathLike] = None) -> Path:
    """The nearest folder (walking up) that has .relaykit/, else the nearest git root, else start."""
    here = Path(start or os.getcwd()).resolve()
    for d in [here, *here.parents]:
        if (d / RK_DIR).is_dir():
            return d
    for d in [here, *here.parents]:
        if (d / ".git").exists():
            return d
    return here


def rk_dir(root: Path) -> Path:
    return Path(root) / RK_DIR


def relays_dir(root: Path) -> Path:
    return rk_dir(root) / "relays"


def resolve_relay_dir(root: Path, name_or_path: str) -> Path:
    """A relay given by name (``.relaykit/relays/<name>``) or by a path to its folder."""
    p = Path(name_or_path).expanduser()
    if not p.is_absolute():
        cand = (Path(root) / p)
        if (cand / PROGRESS_FILE).exists() or (cand / PROMPT_FILE).exists():
            return cand.resolve()
    elif (p / PROGRESS_FILE).exists() or (p / PROMPT_FILE).exists():
        return p.resolve()
    return (relays_dir(root) / str(name_or_path)).resolve()


def list_relays(root: Path) -> list[Path]:
    d = relays_dir(root)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_dir() and (p / PROGRESS_FILE).exists())


def logs_dir(root: Path, relay_dir: Path) -> Path:
    d = rk_dir(root) / "logs" / Path(relay_dir).name
    d.mkdir(parents=True, exist_ok=True)
    return d


def rel(path: Path, root: Path) -> str:
    """``path`` relative to ``root`` with forward slashes (stable in prompts on every OS)."""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(path).resolve().as_posix()
