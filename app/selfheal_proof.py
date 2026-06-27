"""Self-heal proof on the STRUCTURAL mutation (move_dispute_to_cases).

Learn a skill on baseline (verify it replays at 0 CU), then replay it on the mutated arena
where disputing is moved out of the row menu into a separate Cases flow. Cached crops for the
changed steps MISS → each escalates ONE step to CU (re-ground + patch in place). This measures
how far single-step self-heal carries a multi-step structural change, and whether the
deterministic checker still passes.

  python -m app.selfheal_proof
"""
import requests
from playwright.sync_api import sync_playwright

from .config import APP_URL, VIEWPORT
from .cu_runner import run_task
from .skill_compiler import compile_skill
from .replay_engine import replay_skill
from .tasks import HERO
from . import checker

MAX_COLD = 4


def _reset(variant: str) -> None:
    requests.post(f"{APP_URL}/reset?variant={variant}", timeout=5)


def _learn(page):
    """Cold-run the hero on baseline until one checker-verified success, then compile."""
    for attempt in range(1, MAX_COLD + 1):
        _reset("baseline")
        page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
        t = run_task(HERO, page); t.success = checker.check(HERO)
        print(f"  cold attempt {attempt}: {t.n_steps} steps, {'PASS' if t.success else 'FAIL'}")
        if t.success:
            return compile_skill(t)
    raise SystemExit("no verified cold success to learn from")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_context(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]}).new_page()

        print("LEARN on baseline:")
        skill = _learn(page)
        print(f"  compiled {len(skill.steps)} steps, "
              f"{sum(1 for s in skill.steps if s.get('crop_b64'))} crops\n")

        print("REPLAY on baseline (sanity → expect 0 CU):")
        _reset("baseline"); page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
        r1 = replay_skill(skill, page, verbose=True, heal=False)
        ok1 = checker.check(HERO)
        print(f"  → CU {r1['cu_calls']}, checker {'PASS' if ok1 else 'FAIL'}\n")

        print("REPLAY on move_dispute_to_cases (STRUCTURAL → expect misses + heal):")
        _reset("move_dispute_to_cases"); page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
        r2 = replay_skill(skill, page, verbose=True, heal=True)
        ok2 = checker.check(HERO)
        print(f"  → CU {r2['cu_calls']} (escalations {len(r2['escalations'])}), "
              f"checker {'PASS' if ok2 else 'FAIL'}")
        browser.close()

    print("\n=== SELF-HEAL SUMMARY ===")
    print(f"baseline replay:           {r1['cu_calls']} CU over {len(skill.steps)} steps, "
          f"{'PASS' if ok1 else 'FAIL'}")
    print(f"structural replay (heal):  {r2['cu_calls']} CU over {len(skill.steps)} steps, "
          f"{'PASS' if ok2 else 'FAIL'}")
    if r2["escalations"]:
        print("escalated steps:", [e["step"] + 1 for e in r2["escalations"]])


if __name__ == "__main__":
    main()
