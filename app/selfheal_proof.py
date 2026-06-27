"""Two-tier self-heal proof on the hardened arena.

Tier 0  baseline replay ................. 0 CU, PASS        (amortized)
Tier 1  relabel_export (cosmetic) ....... heal, PASS        (heals shallow drift for free)
Tier 2  move_dispute_to_cases (struct) .. cheap-heal FAIL   (checker refuses the half-heal)
        -> recompile on the new UI ...... 0 CU, PASS        (re-amortized, never a fake win)

Uses a GOAL-oriented intent + max_turns=30 (structural flows run long). Tier 1 auto-skips if
the relabel_export variant isn't in the arena yet.

  python -m app.selfheal_proof
"""
import copy
import requests
from dataclasses import replace
from playwright.sync_api import sync_playwright

from .config import APP_URL, VIEWPORT
from .cu_runner import run_task
from .skill_compiler import compile_skill
from .replay_engine import replay_skill
from .tasks import HERO
from . import checker
from .controlled_app import state as arena_state

GOAL = replace(HERO, intent=(
    "This is a local QA billing app you own at localhost (no real money or accounts). "
    "Dispute the unpaid Acme Corp invoice for over $500, choosing the reason 'Duplicate charge'. "
    "Then add the note 'duplicate charge' to that same invoice and export its receipt."))

MAX_COLD = 4
TURNS = 30


def _reset(variant: str) -> None:
    requests.post(f"{APP_URL}/reset?variant={variant}", timeout=5)


def _learn(page, variant: str, label: str):
    """Cold-run the goal intent on `variant` until one checker-verified success, then compile."""
    for attempt in range(1, MAX_COLD + 1):
        _reset(variant)
        page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
        t = run_task(GOAL, page, max_turns=TURNS); t.success = checker.check(GOAL)
        print(f"  [{label}] cold {attempt}: {t.n_steps} steps, {'PASS' if t.success else 'FAIL'}", flush=True)
        if t.success:
            return compile_skill(t)
    return None


def _replay(page, skill, variant: str, heal: bool):
    _reset(variant)
    page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
    res = replay_skill(skill, page, verbose=True, heal=heal)
    return res, checker.check(GOAL)


def main():
    has_cosmetic = "relabel_export" in arena_state.VARIANTS
    rows = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        page = b.new_context(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]}).new_page()

        print("LEARN skill on baseline:")
        skill = _learn(page, "baseline", "learn")
        if skill is None:
            raise SystemExit("could not learn a baseline skill")
        print(f"  -> {len(skill.steps)} steps, "
              f"{sum(1 for s in skill.steps if s.get('crop_b64'))} crops\n")

        print("TIER 0  baseline replay (expect 0 CU):")
        r, ok = _replay(page, skill, "baseline", heal=False)
        rows.append(("baseline replay", r["cu_calls"], ok))
        print(f"  -> {r['cu_calls']} CU, {'PASS' if ok else 'FAIL'}\n")

        if has_cosmetic:
            print("TIER 1  relabel_export (cosmetic) replay + heal (expect heal, PASS):")
            r, ok = _replay(page, copy.deepcopy(skill), "relabel_export", heal=True)
            rows.append(("cosmetic cheap-heal", r["cu_calls"], ok))
            print(f"  -> {r['cu_calls']} CU (escalations {len(r['escalations'])}), {'PASS' if ok else 'FAIL'}\n")
        else:
            print("TIER 1  relabel_export not in arena — SKIPPED (ask ikjun to add it)\n")

        print("TIER 2  move_dispute_to_cases (structural) replay + heal (expect FAIL):")
        r, ok = _replay(page, copy.deepcopy(skill), "move_dispute_to_cases", heal=True)
        rows.append(("structural cheap-heal", r["cu_calls"], ok))
        print(f"  -> {r['cu_calls']} CU (escalations {len(r['escalations'])}), {'PASS' if ok else 'FAIL'}")

        if not ok:
            print("\n  checker REFUSED the half-heal -> RECOMPILE on move_dispute_to_cases:")
            skill2 = _learn(page, "move_dispute_to_cases", "recompile")
            if skill2 is None:
                print("  recompile could not learn a structural skill")
                rows.append(("structural recompile", "-", False))
            else:
                print(f"  -> recompiled {len(skill2.steps)} steps")
                r3, ok3 = _replay(page, skill2, "move_dispute_to_cases", heal=False)
                rows.append(("structural recompiled replay", r3["cu_calls"], ok3))
                print(f"  -> {r3['cu_calls']} CU, {'PASS' if ok3 else 'FAIL'}")
        b.close()

    print("\n=== TWO-TIER SELF-HEAL SUMMARY ===")
    for name, cu, ok in rows:
        print(f"  {name:<32} {str(cu):>3} CU   {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
