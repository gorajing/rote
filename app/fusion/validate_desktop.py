"""Cross-device validation: the FUSED dispatch driving the macOS DESKTOP at 0 CU, artifact-verified.

Lowers Shah's create_word_file macro into a FusedSkill and replays it through the SAME
app.fusion.dispatch + DesktopExecutor + ArtifactVerifier that drive the browser — proving the
dispatcher is truly surface-agnostic: zero model calls, success gated on the real .docx on disk
(not self-report). All 11 macro steps are keyboard ops, so every one routes to the keyboard tier.

REQUIRES macOS: Screen Recording + Accessibility permissions for THIS terminal, and Microsoft
Word installed. Run with hands OFF the mouse/keyboard.

  python -m app.fusion.validate_desktop --probe   # permissions only, no automation
  python -m app.fusion.validate_desktop           # full fused desktop replay
"""
import os
import json
import time
import argparse
import subprocess

from .contract import FusedSkill
from .compiler import _macro_to_steps
from .dispatch import replay
from .desktop_executor import DesktopExecutor
from .verifier import ArtifactVerifier
from .. import desktop_cu

_HERE = os.path.dirname(__file__)
MACRO_PATH = os.path.normpath(
    os.path.join(_HERE, "..", "..", "database", "skills", "create_word_file.macro.json"))
DOCX = os.path.expanduser("~/Desktop/gemini.docx")
NEEDLE = "Hello from Gemini Computer Use"


def _reset_artifact():
    """Quit Word without saving and remove any prior gemini.docx (+ lock file) for a clean run."""
    subprocess.run(["osascript", "-e", 'tell application "Microsoft Word" to quit saving no'],
                   check=False, capture_output=True)
    time.sleep(2)
    for p in (DOCX, os.path.expanduser("~/Desktop/~$gemini.docx")):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="check permissions only, no automation")
    args = ap.parse_args()

    print("=== permission probe (Screen Recording + Accessibility) ===")
    if not desktop_cu.probe():
        print("\nBLOCKED — grant Screen Recording + Accessibility to this terminal in\n"
              "System Settings -> Privacy & Security, then re-run.")
        raise SystemExit(1)
    if args.probe:
        print("probe OK — permissions are in place.")
        return

    macro = json.load(open(MACRO_PATH))
    skill = FusedSkill(
        name=macro.get("name", "create_word_file"), surface="desktop",
        target=macro.get("app", "Microsoft Word"), params=dict(macro.get("params", {})),
        steps=_macro_to_steps(macro),
        verify={"kind": "docx", "path": DOCX, "contains": NEEDLE},
    )
    print(f"fused desktop skill {skill.name!r}: {len(skill.steps)} steps "
          f"{[s.primitive for s in skill.steps]}")

    _reset_artifact()
    print("\nReplaying through dispatch + DesktopExecutor (hands OFF the mouse/keyboard)...\n")
    res = replay(skill, DesktopExecutor(), ArtifactVerifier())

    print("\n=== FUSED DESKTOP REPLAY (cross-device) ===")
    print(f"CU calls: {res['cu_calls']}   verified (~/Desktop/gemini.docx): {res['verified']}   "
          f"needs_recompile: {res['needs_recompile']}")
    print(f"artifact exists: {os.path.exists(DOCX)}")
    for sr in res["steps"]:
        print(f"  [{sr.index}] {sr.primitive:<8} tier={sr.tier:<8} cu={sr.cu_calls} ok={sr.ok}")


if __name__ == "__main__":
    main()
