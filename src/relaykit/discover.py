"""``relaykit scan``: a fast, deterministic profile of any codebase.

It reads manifests, scripts, CI files and instruction files (no network, no code execution) and
writes ``.relaykit/project.json`` plus a first ``.relaykit/PROJECT.md``. The relaykit-init skill
(or ``relaykit init --deep``) then has an agent VERIFY the commands by running them and fill in what
only reading the code reveals (architecture, conventions, the test baseline)."""
from __future__ import annotations

import json
import os
import platform
import re
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from . import gitops, tomlio

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", ".env", "__pycache__", "dist", "build", "target",
             ".next", ".nuxt", ".turbo", ".cache", "coverage", ".relaykit", ".idea", ".vscode", "site-packages",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox", ".gradle", ".terraform", "Pods",
             ".dart_tool", ".svelte-kit", "out", ".parcel-cache", ".yarn", "bower_components", ".expo"}
LANG_BY_EXT = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript",
    ".mjs": "JavaScript", ".cjs": "JavaScript", ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".kts": "Kotlin", ".cs": "C#", ".fs": "F#", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".m": "Objective-C",
    ".c": "C", ".h": "C/C++ header", ".cpp": "C++", ".cc": "C++", ".hpp": "C++", ".ex": "Elixir", ".exs": "Elixir",
    ".erl": "Erlang", ".scala": "Scala", ".dart": "Dart", ".lua": "Lua", ".luau": "Luau", ".r": "R", ".jl": "Julia",
    ".sh": "Shell", ".ps1": "PowerShell", ".sql": "SQL", ".vue": "Vue", ".svelte": "Svelte", ".html": "HTML",
    ".css": "CSS", ".scss": "SCSS", ".zig": "Zig", ".nim": "Nim", ".hs": "Haskell", ".ml": "OCaml", ".clj": "Clojure",
    ".tf": "Terraform", ".sol": "Solidity", ".gd": "GDScript",
}
INSTRUCTION_FILES = ["AGENTS.md", "AGENTS.override.md", "CLAUDE.md", "CLAUDE.local.md", "GEMINI.md", ".cursorrules",
                     ".windsurfrules", ".github/copilot-instructions.md", "CONVENTIONS.md", "CONTRIBUTING.md",
                     ".kiro/steering", ".cursor/rules", ".clinerules", "QWEN.md"]
DEPLOY_HINTS = ["Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "fly.toml", "vercel.json",
                "netlify.toml", "render.yaml", "Procfile", "app.yaml", "serverless.yml", "wrangler.toml", "k8s",
                "helm", "terraform", "ansible", ".github/workflows"]
FORMAT_HINTS = [".editorconfig", ".prettierrc", ".prettierrc.json", ".prettierrc.js", "prettier.config.js",
                "biome.json", ".eslintrc", ".eslintrc.json", ".eslintrc.js", "eslint.config.js", "eslint.config.mjs",
                "ruff.toml", ".ruff.toml", ".flake8", "setup.cfg", ".rubocop.yml", "rustfmt.toml", ".golangci.yml",
                ".clang-format", ".stylelintrc", "mypy.ini", ".pre-commit-config.yaml"]


def _read(path: Path, limit: int = 400_000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _walk(root: Path, max_files: int = 60_000):
    n = 0
    csharp = any(root.glob("*.sln")) or any(root.glob("*.csproj"))
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not (csharp and d in ("bin", "obj"))
                       and not d.endswith(".egg-info")]
        for f in filenames:
            yield Path(dirpath) / f
            n += 1
            if n >= max_files:
                return


def _cmd(purpose: str, command: str, source: str, confidence: str = "medium") -> dict:
    return {"purpose": purpose, "command": command, "source": source, "confidence": confidence, "verified": False}


def scan(root: Path) -> dict:
    root = Path(root).resolve()
    t0 = time.time()
    exts: Counter = Counter()
    files = 0
    crlf = lf = 0
    sampled = 0
    tests_dirs = set()
    big_files = []
    for p in _walk(root):
        files += 1
        ext = p.suffix.lower()
        if ext in LANG_BY_EXT:
            exts[LANG_BY_EXT[ext]] += 1
        parts = {x.lower() for x in p.relative_to(root).parts[:-1]}
        if parts & {"test", "tests", "__tests__", "spec", "specs"}:
            tests_dirs.add(next(x for x in p.relative_to(root).parts[:-1] if x.lower() in {"test", "tests", "__tests__", "spec", "specs"}))
        if ext in LANG_BY_EXT and sampled < 400:
            try:
                data = p.read_bytes()[:65536]
                sampled += 1
                c = data.count(b"\r\n")
                crlf += c
                lf += data.count(b"\n") - c
                if p.stat().st_size > 400_000:
                    big_files.append(p.relative_to(root).as_posix())
            except OSError:
                pass

    info: dict = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "root": str(root), "name": root.name,
        "os": f"{platform.system()} {platform.release()}", "python": platform.python_version(),
        "files_scanned": files, "languages": exts.most_common(12),
        "line_endings": "CRLF" if crlf and not lf else "LF" if lf and not crlf else ("mixed" if crlf and lf else "unknown"),
        "manifests": [], "package_manager": None, "commands": [], "ci": [], "instruction_files": [],
        "format_configs": [], "deploy_hints": [], "test_dirs": sorted(tests_dirs)[:10], "monorepo": [],
        "big_source_files": big_files[:10], "env_files": [], "git": {}, "notes": [],
    }

    def has(rel: str) -> bool:
        return (root / rel).exists()

    for f in INSTRUCTION_FILES:
        if has(f):
            info["instruction_files"].append(f)
    for f in FORMAT_HINTS:
        if has(f):
            info["format_configs"].append(f)
    for f in DEPLOY_HINTS:
        if has(f):
            info["deploy_hints"].append(f)
    info["env_files"] = sorted(p.name for p in root.glob(".env*") if p.is_file())

    cmds = info["commands"]
    # ---- JavaScript / TypeScript
    if has("package.json"):
        info["manifests"].append("package.json")
        try:
            pkg = json.loads(_read(root / "package.json"))
        except ValueError:
            pkg = {}
        pm = "npm"
        for lock, name in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"), ("bun.lock", "bun")):
            if has(lock):
                pm = name
        info["package_manager"] = pm
        cmds.append(_cmd("install", f"{pm} install", "package.json", "high"))
        scripts = pkg.get("scripts") or {}
        for key, purpose in (("test", "test"), ("build", "build"), ("lint", "lint"), ("typecheck", "typecheck"),
                             ("type-check", "typecheck"), ("check", "check"), ("format", "format"), ("dev", "run"),
                             ("start", "run"), ("e2e", "e2e test")):
            if key in scripts:
                run = f"{pm} test" if (key == "test" and pm == "npm") else f"{pm} run {key}"
                cmds.append(_cmd(purpose, run, f"package.json scripts.{key} = {scripts[key][:80]}", "high"))
        if pkg.get("workspaces"):
            info["monorepo"].append("package.json workspaces")
    for f, label in (("pnpm-workspace.yaml", "pnpm workspaces"), ("turbo.json", "turborepo"), ("nx.json", "nx"),
                     ("lerna.json", "lerna")):
        if has(f):
            info["monorepo"].append(label)
    if has("tsconfig.json") and not any(c["purpose"] == "typecheck" for c in cmds):
        cmds.append(_cmd("typecheck", "npx tsc --noEmit", "tsconfig.json", "medium"))

    # ---- Python
    py_tool = None
    if has("pyproject.toml"):
        info["manifests"].append("pyproject.toml")
        try:
            pp = tomlio.loads(_read(root / "pyproject.toml"))
        except Exception:
            pp = {}
        tool = pp.get("tool") or {}
        if "poetry" in tool:
            py_tool = "poetry"
        elif has("uv.lock") or "uv" in tool:
            py_tool = "uv"
        elif "hatch" in tool:
            py_tool = "hatch"
        elif "pdm" in tool:
            py_tool = "pdm"
        if "ruff" in tool:
            info["format_configs"].append("pyproject [tool.ruff]")
        if "pytest" in tool:
            info["notes"].append("pytest configured in pyproject.toml")
    for req in ("requirements.txt", "requirements-dev.txt", "requirements/dev.txt", "Pipfile", "setup.py", "setup.cfg"):
        if has(req):
            info["manifests"].append(req)
    is_py = any(m in info["manifests"] for m in ("pyproject.toml", "requirements.txt", "setup.py", "Pipfile", "setup.cfg"))
    if is_py:
        prefix = {"poetry": "poetry run ", "uv": "uv run ", "hatch": "hatch run ", "pdm": "pdm run "}.get(py_tool or "", "")
        if py_tool == "poetry":
            cmds.append(_cmd("install", "poetry install", "pyproject [tool.poetry]", "high"))
        elif py_tool == "uv":
            cmds.append(_cmd("install", "uv sync", "uv.lock", "high"))
        elif has("requirements.txt"):
            cmds.append(_cmd("install", "pip install -r requirements.txt", "requirements.txt", "high"))
        elif has("pyproject.toml"):
            cmds.append(_cmd("install", "pip install -e .", "pyproject.toml", "medium"))
        reqs = _read(root / "requirements.txt") + _read(root / "requirements-dev.txt") + _read(root / "pyproject.toml")
        if has("pytest.ini") or has("conftest.py") or "pytest" in reqs or tests_dirs:
            cmds.append(_cmd("test", f"{prefix}python -m pytest", "pytest config / tests dir", "medium"))
        if has("tox.ini"):
            cmds.append(_cmd("test (matrix)", "tox", "tox.ini", "medium"))
        if has("noxfile.py"):
            cmds.append(_cmd("test (sessions)", "nox", "noxfile.py", "medium"))
        if "ruff" in reqs or has("ruff.toml") or any("ruff" in c for c in info["format_configs"]):
            cmds.append(_cmd("lint", f"{prefix}ruff check .", "ruff config", "medium"))
        if "mypy" in reqs or has("mypy.ini"):
            cmds.append(_cmd("typecheck", f"{prefix}mypy .", "mypy config", "low"))

    # ---- other ecosystems
    if has("Cargo.toml"):
        info["manifests"].append("Cargo.toml")
        cmds += [_cmd("build", "cargo build", "Cargo.toml", "high"), _cmd("test", "cargo test", "Cargo.toml", "high"),
                 _cmd("lint", "cargo clippy --all-targets", "Cargo.toml", "medium")]
    if has("go.mod"):
        info["manifests"].append("go.mod")
        cmds += [_cmd("build", "go build ./...", "go.mod", "high"), _cmd("test", "go test ./...", "go.mod", "high"),
                 _cmd("lint", "go vet ./...", "go.mod", "medium")]
    if has("pom.xml"):
        info["manifests"].append("pom.xml")
        cmds += [_cmd("build", "mvn -q -DskipTests package", "pom.xml", "medium"), _cmd("test", "mvn -q test", "pom.xml", "high")]
    for g in ("build.gradle", "build.gradle.kts"):
        if has(g):
            info["manifests"].append(g)
            gw = "./gradlew" if has("gradlew") else "gradle"
            cmds += [_cmd("build", f"{gw} build -x test", g, "medium"), _cmd("test", f"{gw} test", g, "high")]
            break
    slns = list(root.glob("*.sln")) + list(root.glob("*.csproj"))
    if slns:
        info["manifests"].append(slns[0].name)
        cmds += [_cmd("build", "dotnet build", slns[0].name, "high"), _cmd("test", "dotnet test", slns[0].name, "high")]
    if has("Gemfile"):
        info["manifests"].append("Gemfile")
        cmds.append(_cmd("install", "bundle install", "Gemfile", "high"))
        cmds.append(_cmd("test", "bundle exec rspec" if has("spec") else "bundle exec rake test", "Gemfile", "medium"))
    if has("composer.json"):
        info["manifests"].append("composer.json")
        cmds += [_cmd("install", "composer install", "composer.json", "high"),
                 _cmd("test", "vendor/bin/phpunit", "composer.json", "medium")]
    if has("mix.exs"):
        info["manifests"].append("mix.exs")
        cmds += [_cmd("install", "mix deps.get", "mix.exs", "high"), _cmd("test", "mix test", "mix.exs", "high")]
    if has("pubspec.yaml"):
        info["manifests"].append("pubspec.yaml")
        cmds += [_cmd("install", "flutter pub get", "pubspec.yaml", "medium"), _cmd("test", "flutter test", "pubspec.yaml", "medium")]
    if has("default.project.json") or has("wally.toml") or has("rokit.toml") or has("aftman.toml"):
        info["manifests"].append("Roblox (Rojo/Wally)")
        cmds.append(_cmd("build", "rojo build -o build.rbxlx", "Rojo project", "low"))

    # ---- task runners
    if has("Makefile"):
        info["manifests"].append("Makefile")
        targets = re.findall(r"^([A-Za-z][\w.-]*)\s*:(?!=)", _read(root / "Makefile"), re.M)
        for t in ("test", "check", "lint", "build", "all", "ci"):
            if t in targets:
                cmds.append(_cmd(t if t != "all" else "build", f"make {t}", "Makefile", "high"))
    if has("justfile") or has("Justfile"):
        jf = root / ("justfile" if has("justfile") else "Justfile")
        info["manifests"].append(jf.name)
        recipes = re.findall(r"^([A-Za-z][\w-]*)\s*(?:[^:\n=]*)?:(?!=)", _read(jf), re.M)
        for t in ("test", "check", "lint", "build", "ci"):
            if t in recipes:
                cmds.append(_cmd(t, f"just {t}", jf.name, "high"))
    if has("Taskfile.yml") or has("Taskfile.yaml"):
        info["manifests"].append("Taskfile")

    # ---- CI evidence
    wf = root / ".github" / "workflows"
    if wf.is_dir():
        for f in sorted(wf.glob("*.y*ml"))[:12]:
            runs = re.findall(r"^\s*(?:-\s*)?run:\s*\|?\s*(.+)$", _read(f), re.M)
            info["ci"].append({"file": f.relative_to(root).as_posix(), "runs": [r.strip() for r in runs if r.strip() and r.strip() != "|"][:15]})
    for f in (".gitlab-ci.yml", ".circleci/config.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml", "Jenkinsfile"):
        if has(f):
            info["ci"].append({"file": f, "runs": []})

    # ---- git
    if gitops.is_repo(root):
        info["git"] = {"branch": gitops.branch(root), "head": gitops.head(root),
                       "dirty_files": len(gitops.dirty_files(root)),
                       "remotes": [ln.split()[1] for ln in gitops.git(root, "remote", "-v").stdout.splitlines() if ln.endswith("(push)")]}
    info["scan_seconds"] = round(time.time() - t0, 2)

    # de-duplicate commands (same purpose+command)
    seen = set()
    uniq = []
    for c in cmds:
        k = (c["purpose"], c["command"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    info["commands"] = uniq
    return info


def render_profile(info: dict, previous: Optional[str] = None) -> str:
    L = []
    L.append(f"# Project profile: {info['name']}")
    L.append("")
    L.append(f"> Generated by `relaykit scan` on {info['generated_at']}. Every relay session reads this file first.")
    L.append("> Commands marked unverified are guesses from manifests and CI. Refine with the relaykit-init skill")
    L.append("> (`/relaykit:init` in Claude Code, `$relaykit-init` in Codex) or `relaykit init --deep`, which runs them.")
    L.append("")
    L.append("## At a glance")
    langs = ", ".join(f"{name} ({n})" for name, n in info["languages"][:8]) or "unknown"
    L.append(f"- **Path:** `{info['root']}` · **OS:** {info['os']}")
    L.append(f"- **Languages (files):** {langs}")
    L.append(f"- **Files scanned:** {info['files_scanned']:,} · **Line endings:** {info['line_endings']}")
    L.append(f"- **Manifests:** {', '.join(info['manifests']) or 'none found'}")
    if info["monorepo"]:
        L.append(f"- **Monorepo:** {', '.join(info['monorepo'])}")
    g = info.get("git") or {}
    if g:
        L.append(f"- **Git:** branch `{g.get('branch')}` at `{g.get('head')}`, {g.get('dirty_files', 0)} uncommitted file(s) at scan time")
    L.append("")
    L.append("## Commands")
    L.append("| Purpose | Command | Found in | Verified |")
    L.append("|---|---|---|---|")
    for c in info["commands"]:
        L.append(f"| {c['purpose']} | `{c['command']}` | {c['source']} | {'yes' if c.get('verified') else 'no'} |")
    if not info["commands"]:
        L.append("| ? | (none detected — a session must work these out and record them here) | | no |")
    L.append("")
    L.append("## Instruction files every session must read")
    if info["instruction_files"]:
        for f in info["instruction_files"]:
            L.append(f"- `{f}`")
    else:
        L.append("- (none found)")
    L.append("")
    if info["ci"]:
        L.append("## CI (what the project itself runs)")
        for c in info["ci"]:
            L.append(f"- `{c['file']}`" + (": " + "; ".join(f"`{r[:100]}`" for r in c["runs"][:6]) if c["runs"] else ""))
        L.append("")
    L.append("## Conventions")
    if info["format_configs"]:
        L.append(f"- Formatter/linter configs: {', '.join(f'`{x}`' for x in info['format_configs'])}. Follow them; don't reformat files you didn't change.")
    L.append("- Match the surrounding code: naming, comment density, error handling, test style.")
    L.append("")
    L.append("## Hazards")
    if info["line_endings"] == "mixed":
        L.append("- **Mixed line endings.** Edit with targeted replacements; never rewrite whole files (it flips every line).")
    if g.get("dirty_files"):
        L.append(f"- **The tree had {g['dirty_files']} uncommitted file(s) at scan time.** They may be someone else's work in progress: never revert or sweep them into a commit.")
    if info["env_files"]:
        L.append(f"- **Secrets files present** ({', '.join(info['env_files'])}). Never print, copy, commit or edit them.")
    if info["big_source_files"]:
        L.append(f"- **Very large source files** ({', '.join(info['big_source_files'][:5])}): read them in ranges, not whole.")
    if info["files_scanned"] >= 60_000:
        L.append("- **Very large repo** (scan stopped at 60,000 files): search before reading; never list everything.")
    L.append("- Never deploy, publish, push or touch production from a relay session — log it under Owner handoff.")
    L.append("")
    L.append("## Owner-only")
    if info["deploy_hints"]:
        L.append(f"- Deploy/infra config present: {', '.join(f'`{x}`' for x in info['deploy_hints'])}. Changing it is fine when the plan says so; RUNNING a deploy is the owner's.")
    L.append("- Publishing packages, pushing, releasing, rotating credentials, paid services, production data.")
    L.append("")
    L.append("## Architecture notes")
    L.append("(to be filled in by the init session: the main entry points, the modules that matter, where the tests live, how a request/command flows)")
    L.append("")
    L.append("## Test baseline")
    L.append("(to be filled in by the init session: the exact test command, how long it takes, and which tests already fail before any change)")
    L.append("")
    if previous:
        keep = _keep_human_sections(previous)
        if keep:
            L.append(keep)
    return "\n".join(L).rstrip() + "\n"


def _keep_human_sections(previous: str) -> str:
    """Re-scans must not wipe what a person or an init session wrote: keep sections after a
    ``<!-- relaykit:keep -->`` marker verbatim."""
    idx = previous.find("<!-- relaykit:keep -->")
    return previous[idx:].strip() if idx >= 0 else ""
