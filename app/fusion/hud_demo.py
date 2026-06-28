"""The notch HUD narrating a FUSED replay live — the demo instrument.

Runs the desktop Word skill through dispatch.replay (DesktopExecutor + ArtifactVerifier) while
Shah's notch island shows the live CU-call count + per-step tier, then "N CU · verified ✓".
This is the visual money-shot: the count stays 0 the whole way, then the checker confirms it.

  python -m app.fusion.hud_demo

Needs the desktop session (the notch is a macOS overlay), Word, and the same permissions as
validate_desktop. Hands off the mouse/keyboard.
"""
import json

from .contract import FusedSkill
from .compiler import _macro_to_steps
from .dispatch import replay
from .desktop_executor import DesktopExecutor
from .verifier import ArtifactVerifier
from .validate_desktop import MACRO_PATH, DOCX, NEEDLE, _reset_artifact
from .. import desktop_cu
from ..notch import NotchIsland


def main():
    if not desktop_cu.probe():
        raise SystemExit("Fix Screen Recording / Accessibility permissions first.")

    macro = json.load(open(MACRO_PATH))
    skill = FusedSkill(
        name=macro.get("name", "create_word_file"), surface="desktop",
        target=macro.get("app", "Microsoft Word"), params=dict(macro.get("params", {})),
        steps=_macro_to_steps(macro),
        verify={"kind": "docx", "path": DOCX, "contains": NEEDLE},
    )
    _reset_artifact()

    hud = NotchIsland()
    result = {}

    def on_step(i, total, cu_calls, sr):
        hud.step(i + 1, total, f"CU {cu_calls} · {sr.tier}")

    def work():
        res = replay(skill, DesktopExecutor(), ArtifactVerifier(), on_step=on_step)
        result.update(res)
        hud.finish(f"{res['cu_calls']} CU · {'verified ✓' if res['verified'] else 'FAIL ✗'}")

    hud.run(work)   # blocks on the AppKit run loop until the HUD auto-exits
    print(f"\nHUD demo done: CU={result.get('cu_calls')}  verified={result.get('verified')}")


if __name__ == "__main__":
    main()
