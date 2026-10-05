"""Creating relay folders from a plan."""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Optional

from . import paths

TEMPLATES = Path(__file__).resolve().parent / "templates"
_SLUG = re.compile(r"[^a-z0-9-]+")


def slugify(name: str) -> str:
    s = _SLUG.sub("-", name.strip().lower()).strip("-")
    return s[:60] or "relay"


def render(template: str, values: dict) -> str:
    """``{{key}}`` substitution only — never str.format (plan text is full of braces)."""
    out = template
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def load_template(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


_PHASE_LINE = re.compile(r"^\s*-\s*\[[ x~]\]\s*\*\*\s*(Phase\s+[\w.]+)\s*[—–:-]+\s*(.+?)\s*\*\*", re.I | re.M)
_PHASE_HEAD = re.compile(r"^#{2,4}\s*(Phase\s+[\w.]+)\s*[—–:-]+\s*(.+?)\s*$", re.I | re.M)


def plan_phases(plan_text: str) -> list:
    """[(label, title)] from 'Progress at a glance' checkboxes, else from '### Phase N — title' headings."""
    found = [(m.group(1).strip(), re.sub(r"\*\*.*$", "", m.group(2)).strip()) for m in _PHASE_LINE.finditer(plan_text)]
    if not found:
        found = [(m.group(1).strip(), m.group(2).strip()) for m in _PHASE_HEAD.finditer(plan_text)]
    seen, out = set(), []
    for lab, title in found:
        if lab.lower() not in seen:
            seen.add(lab.lower())
            out.append((lab, title))
    return out


def create(root: Path, name: str, *, title: str = "", goal: str = "", plan_src: Optional[Path] = None,
           phase_zero: bool = True, final_review: bool = True, extra_rules: str = "",
           extra_files: Optional[list] = None, force: bool = False, handoff_pct: float = 35,
           hard_pct: float = 45, early_handoff_pct: float = 20) -> Path:
    root = Path(root).resolve()
    slug = slugify(name)
    d = paths.relays_dir(root) / slug
    if d.exists() and (d / paths.PROGRESS_FILE).exists() and not force:
        raise FileExistsError(f"relay '{slug}' already exists at {d}")
    d.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    title = title or name.replace("-", " ").strip().title()
    rel = lambda p: paths.rel(p, root)  # noqa: E731

    plan_path = d / paths.PLAN_FILE
    if plan_src:
        src = Path(plan_src)
        if src.resolve() != plan_path.resolve():
            shutil.copyfile(src, plan_path)
    elif not plan_path.exists():
        plan_path.write_text(render(load_template("PLAN.md"), {
            "title": title, "today": today, "relay_rel": rel(d), "goal": goal or "<what this relay must achieve>",
        }), encoding="utf-8")
    plan_text = plan_path.read_text(encoding="utf-8", errors="replace")

    phases = []
    if phase_zero:
        phases.append(("0", "Orientation + baseline (no product changes)"))
    for i, (lab, t) in enumerate(plan_phases(plan_text), start=1):
        num = re.sub(r"(?i)^phase\s+", "", lab)
        phases.append((num, t))
    if final_review:
        phases.append(("R", "Review + final report (adversarial review, fixes, wider test run, final report)"))
    rows = "\n".join(f"| {n} | {t} | todo | |" for n, t in phases) or "| 1 | (see the plan) | todo | |"

    project_md = paths.rk_dir(root) / paths.PROJECT_MD
    extra_rows = ""
    for f in extra_files or []:
        extra_rows += f"| `{f}` | Required reading for this relay |\n"
    prompt_vals = {
        "title": title, "progress_rel": rel(d / paths.PROGRESS_FILE), "plan_rel": rel(plan_path),
        "project_rel": rel(project_md), "extra_rules_rows": extra_rows,
        "custom_rules": ("\n" + extra_rules.strip() + "\n") if extra_rules.strip() else "",
        "handoff_pct": f"{handoff_pct:g}", "hard_pct": f"{hard_pct:g}", "early_handoff_pct": f"{early_handoff_pct:g}",
    }
    (d / paths.PROMPT_FILE).write_text(render(load_template("RELAY_PROMPT.md"), prompt_vals), encoding="utf-8")

    first = f"Phase {phases[0][0]} ({phases[0][1]})" if phases else "Phase 1"
    if phase_zero:
        next_action = (
            "**Phase 0 — orientation + baseline. Change no product code in this phase.**\n"
            f"1. Snapshot `git status --short` and the current commit under \"Baseline\". Anything already modified is NOT "
            f"this relay's: list it and never touch it.\n"
            f"2. Read `{rel(project_md)}` and run its build/test commands to record the **baseline** (pass/fail counts, "
            f"names of failures, how long each took). Split anything over ~10 minutes into chunks. If a command in the "
            f"profile is wrong, fix the profile and say so in the Decision log.\n"
            f"3. Read `{rel(plan_path)}` in full and verify every file, symbol and assumption it names against the CURRENT "
            f"code. Record what you find (with file:line) under \"Orientation notes\" and anything risky under \"Traps\".\n"
            "4. Set Phase 0 to `done` with the evidence, then start Phase 1 in the same session if context allows.")
    else:
        next_action = f"Start {first}: read the plan section for it and begin."
    (d / paths.PROGRESS_FILE).write_text(render(load_template("RELAY_PROGRESS.md"), {
        "title": title, "prompt_rel": rel(d / paths.PROMPT_FILE), "today": today, "first_phase": first,
        "next_action": next_action, "phase_rows": rows,
    }), encoding="utf-8")
    return d
