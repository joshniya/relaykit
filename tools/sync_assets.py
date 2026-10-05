"""Copy the canonical skills (src/relaykit/assets/skills) to the repo-root skills/ folder that the
Claude Code plugin and the Gemini extension read. Run after editing a skill; CI checks they match.

    python tools/sync_assets.py          # copy
    python tools/sync_assets.py --check  # exit 1 if they differ
"""
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "relaykit" / "assets" / "skills"
DST = ROOT / "skills"


def differs() -> list:
    out = []
    for d in sorted(p for p in SRC.iterdir() if p.is_dir()):
        for f in sorted(d.rglob("*")):
            if f.is_file():
                t = DST / f.relative_to(SRC)
                if not t.exists() or not filecmp.cmp(f, t, shallow=False):
                    out.append(str(t.relative_to(ROOT)))
    for t in DST.rglob("*") if DST.exists() else []:
        if t.is_file() and not (SRC / t.relative_to(DST)).exists():
            out.append(str(t.relative_to(ROOT)) + " (stale)")
    return out


if __name__ == "__main__":
    if "--check" in sys.argv:
        bad = differs()
        if bad:
            print("skills/ is out of date: " + ", ".join(bad) + "\nrun: python tools/sync_assets.py")
            sys.exit(1)
        print("skills/ in sync")
        sys.exit(0)
    if DST.exists():
        shutil.rmtree(DST)
    shutil.copytree(SRC, DST)
    print(f"copied {SRC} -> {DST}")
