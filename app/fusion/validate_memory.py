"""The 'it remembers across runs' demo — fusion's cross-run skill memory (the option-b beat).

Run it twice against the arena:
  run 1 (COLD memory): nothing promoted -> learn a skill on the structural arena (cold CU, goal
         intent) -> verify against /state -> PROMOTE it to the fusion registry.
  run 2 (WARM memory): load the promoted skill -> replay at 0 CU, verified -> NO learning, NO model.

So the engine doesn't just self-heal within a run — it persists and promotes the improved skill,
and the next run starts already improved.

  python -m app.fusion.validate_memory            # warm if remembered, else learn + promote
  python -m app.fusion.validate_memory --forget   # ignore memory this run (force a cold learn)
"""
import argparse

from playwright.sync_api import sync_playwright

from ..config import APP_URL, VIEWPORT
from .dispatch import replay
from .browser_executor import BrowserExecutor
from .verifier import make_verifier
from .skill_store import FusionSkillStore
from .validate_selfheal import GOAL, _learn, _reset

SKILL_NAME = f"fused_{GOAL.id}"   # the stable name the compiler assigns + the store keys on


def _open(page, variant="move_dispute_to_cases"):
    _reset(variant)
    page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forget", action="store_true", help="ignore promoted memory this run")
    args = ap.parse_args()

    store = FusionSkillStore()
    remembered = None if args.forget else store.load_active(SKILL_NAME)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_context(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]}).new_page()

        if remembered is not None:
            print(f"WARM: loaded promoted '{SKILL_NAME}' v{remembered.version} from memory.")
            _open(page)
            res = replay(remembered, BrowserExecutor(page), make_verifier(remembered))
            mode, version = "warm (remembered)", remembered.version
            print(f"  replay on structural: CU={res['cu_calls']}  verified={res['verified']}  "
                  f"-> no learning, no recompile")
        else:
            print(f"COLD: no promoted '{SKILL_NAME}' in memory — learning on the structural arena.")
            skill = _learn(page, GOAL, "move_dispute_to_cases", "learn")
            if skill is None:
                browser.close(); raise SystemExit("could not learn a structural skill")
            _open(page)
            res = replay(skill, BrowserExecutor(page), make_verifier(skill))
            mode, version = "cold (learned)", skill.version
            print(f"  learned replay: CU={res['cu_calls']}  verified={res['verified']}")
            if res["verified"]:
                rec = store.save_promoted(skill, verified=True, cu_calls=res["cu_calls"], reason="learned")
                version = rec["version"]
                print(f"  PROMOTED to v{rec['version']} — run again to see it remembered (0-CU).")
        browser.close()

    print(f"\n=== MEMORY DEMO: {mode}, skill v{version}, CU={res['cu_calls']}, verified={res['verified']} ===")


if __name__ == "__main__":
    main()
