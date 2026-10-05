"""Fail if the built wheel is missing the templates or skills (package data is easy to lose)."""
import glob
import sys
import zipfile

wheels = glob.glob("dist/*.whl")
if not wheels:
    sys.exit("no wheel in dist/")
names = zipfile.ZipFile(wheels[0]).namelist()
need = ["relaykit/templates/RELAY_PROMPT.md", "relaykit/templates/RELAY_PROGRESS.md", "relaykit/templates/PLAN.md",
        "relaykit/assets/skills/relaykit-plan/SKILL.md", "relaykit/assets/skills/relaykit-init/SKILL.md",
        "relaykit/assets/skills/relaykit-run/SKILL.md", "relaykit/_hook.py"]
missing = [n for n in need if n not in names]
if missing:
    sys.exit("wheel is missing: " + ", ".join(missing))
print(f"{wheels[0]}: ok ({len(names)} files)")
