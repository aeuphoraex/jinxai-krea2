"""Check the SeaArt app spec against this project's real settings.

Guards the two things that silently break a launch: a spec whose sampler/resolution does
not match what the distilled core needs, and an adult registry adapter leaking into
an app that the platform's incentive program will not accept.

Run:  python launch/check_app_spec.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SPEC = HERE / "app_spec.json"

EXPECTED = {"steps": 8, "cfg": 1.0, "sampler": "euler", "scheduler": "simple"}


def registry_block(app_source: str, name: str) -> str:
    """Text of one CUSTOM_LORAS entry, or "" when the name is not registered."""
    match = re.search(rf'\n    "{re.escape(name)}": \{{(.*?)\n    \}},', app_source, re.S)
    return match.group(1) if match else ""


def main() -> int:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    problems: list[str] = []
    lived: list[str] = []

    for key, want in EXPECTED.items():
        got = spec["sampler"].get(key)
        if got != want:
            problems.append(f"sampler.{key} is {got!r}, the Turbo checkpoint needs {want!r}")
    lived.append(f"sampler {EXPECTED['steps']} steps / cfg {EXPECTED['cfg']} / {EXPECTED['sampler']} + {EXPECTED['scheduler']}")

    for label, (w, h) in spec["resolution"]["presets"].items():
        if w % 16 or h % 16:
            problems.append(f"resolution {label} {w}x{h} is not a multiple of 16")
        if not (512 <= min(w, h) and max(w, h) <= 2048):
            problems.append(f"resolution {label} {w}x{h} is outside 512-2048")
    lived.append(f"{len(spec['resolution']['presets'])} aspect presets, all 16-aligned and in range")

    stack = spec["lora"]["stack"]
    if len(stack) > spec["lora"]["max_stack"]:
        problems.append(f"{len(stack)} stack members exceeds max_stack {spec['lora']['max_stack']}")
    for member in stack:
        name = str(member.get("name", "")).strip()
        if not name or name.lower() == "none":
            continue
        if name.startswith("<"):
            lived.append(f"stack slot is a placeholder ({name}) — fill with a SFW LoRA or None")
            continue
        if float(member.get("strength", 0)) > 0 and not str(member.get("trigger", "")).strip():
            problems.append(f"stack member {name} has strength {member['strength']} but no trigger")
        block = registry_block(app_source, name)
        if not block:
            problems.append(f"stack member {name} is not in CUSTOM_LORAS (typo, or not a registry entry)")
        elif "NSFW" in block:
            problems.append(f"stack member {name} is flagged NSFW in app.py — not publishable on SeaArt")
        elif "trigger" in block and not str(member.get("trigger", "")).strip():
            problems.append(f"stack member {name} needs the registry trigger from app.py")
    if not any(str(m.get("name", "")).strip().lower() not in ("", "none") for m in stack):
        lived.append("stack currently empty — app works with the base model alone")

    workflow = (HERE / spec["platform"]["comfy_workflow"]).resolve()
    if not workflow.exists():
        problems.append(f"workflow missing: {workflow}")
    else:
        wf = json.loads(workflow.read_text(encoding="utf-8"))
        classes = {n["type"] for n in wf["nodes"]}
        lived.append(f"workflow present with {len(wf['nodes'])} nodes ({len(classes)} class types)")
        if "SaveWEBM" in classes:
            lived.append("note: SaveWEBM is experimental upstream — swap for SaveVideo if SeaArt lacks it")

    for tid in [f["id"] for f in spec["inputs"]]:
        if tid not in spec["prompt_template"] and tid not in ("aspect", "strength", "seed"):
            lived.append(f"input '{tid}' is a control, not prompt text")

    if problems:
        print("FAIL launch/app_spec.json")
        for problem in problems:
            print("  -", problem)
        return 1
    print("PASS launch/app_spec.json")
    for line in lived:
        print("  ·", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
