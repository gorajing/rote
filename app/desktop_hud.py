"""Run a macro replay with the Dynamic-Island HUD narrating it live.

  python -m app.desktop_hud --replay database/skills/calc_to_word.macro.json

The HUD (main thread) animates a spinner + step + progress bar by the notch, while the replay
runs on a worker thread — so even multi-second app loads look like active progress, not a freeze.
"""
import json
import argparse

from .desktop_cu import replay, probe
from .hud import Hud


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", required=True, help="path to a macro JSON to replay")
    a = ap.parse_args()

    if not probe():
        raise SystemExit("Fix Screen Recording / Accessibility permissions first.")

    macro = json.load(open(a.replay))
    hud = Hud(title=macro.get("name", "Rote"))

    def work():
        replay(macro, on_step=hud.step)
        hud.finish("Done ✓")

    hud.run(work)


if __name__ == "__main__":
    main()
