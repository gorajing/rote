"""Self-heal on the FUSED engine — the durability half of the browser hero.

learn (baseline) -> TIER 0 baseline replay (0 CU, verified)
                 -> TIER 1 relabel_export (cosmetic): the export menu item changed label, its crop
                           MISSES -> escalate ONE step to CU -> re-grounds + still verified (cheap heal)
                 -> TIER 2 move_dispute_to_cases (structural): the dispute sub-flow moved, cheap-heal
                           is insufficient -> verifier FALSE -> needs_recompile -> recompile (goal-intent
                           cold on the new UI) -> replay 0 CU, verified.

The integrity point: the Verifier reads /state, so a half-heal that doesn't actually dispute the
invoice is caught (needs_recompile), never cached as success.

  python -m app.fusion.validate_selfheal
"""
import requests
from dataclasses import replace
from playwright.sync_api import sync_playwright

from ..config import APP_URL, VIEWPORT
from ..cu_runner import run_task
from ..tasks import HERO
from .. import checker
from .compiler import compile as compile_fused
from .dispatch import replay
from .browser_executor import BrowserExecutor
from .verifier import make_verifier

MAX_COLD = 4
VERIFY = {"kind": "checker", "checker": HERO.checker, "params": dict(HERO.params)}
# Goal-oriented intent for RECOMPILE: the explicit HERO intent names "Mark Disputed", which does
# not exist on the structural variant — so re-learning must describe the GOAL, not the old steps.
GOAL = replace(HERO, intent=(
    "This is a local QA billing app you own at localhost (no real money or accounts). "
    "Dispute the unpaid Acme Corp invoice for over $500, choosing the reason 'Duplicate charge'. "
    "Then add the note 'duplicate charge' to that same invoice and export its receipt."))


def _reset(variant="baseline"):
    requests.post(f"{APP_URL}/reset?variant={variant}", timeout=5)


def _learn(page, task, variant, label):
    """Cold-run `task` on `variant` until one checker-verified success, then lower to a FusedSkill."""
    for attempt in range(1, MAX_COLD + 1):
        _reset(variant); page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
        t = run_task(task, page, max_turns=30); t.success = checker.check(task)
        print(f"  [{label}] cold {attempt}: {t.n_steps} steps, {'PASS' if t.success else 'FAIL'}", flush=True)
        if t.success:
            return compile_fused(t, surface="browser", verify=VERIFY)
    return None


def _replay_on(page, skill, variant, label):
    _reset(variant); page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
    res = replay(skill, BrowserExecutor(page), make_verifier(skill))
    escalated = [sr.index for sr in res["steps"] if sr.tier == "model"]
    print(f"  {label}: CU={res['cu_calls']}  verified={res['verified']}  "
          f"needs_recompile={res['needs_recompile']}  escalated_steps={escalated}", flush=True)
    return res


def main():
    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_context(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]}).new_page()

        print("LEARN baseline FusedSkill (explicit intent):")
        skill = _learn(page, HERO, "baseline", "learn")
        if skill is None:
            browser.close(); raise SystemExit("could not learn a baseline skill")
        crops = sum(1 for s in skill.steps if s.pre and s.pre.crop_b64)
        print(f"  -> {len(skill.steps)} steps, {crops} crops\n")

        print("TIER 0  baseline replay (expect 0 CU, verified):")
        rows.append(("baseline", _replay_on(page, skill, "baseline", "baseline")))

        print("TIER 1  relabel_export — cosmetic drift (expect cheap heal: ~1 CU, verified):")
        rows.append(("relabel_export cheap-heal", _replay_on(page, skill, "relabel_export", "relabel")))

        print("TIER 2  move_dispute_to_cases — structural drift (expect cheap-heal INSUFFICIENT):")
        r2 = _replay_on(page, skill, "move_dispute_to_cases", "structural")
        rows.append(("structural cheap-heal", r2))

        if r2["needs_recompile"]:
            print("\n  checker REFUSED the half-heal -> RECOMPILE on structural (goal intent):")
            skill2 = _learn(page, GOAL, "move_dispute_to_cases", "recompile")
            if skill2 is None:
                print("  recompile could not learn a structural skill")
                rows.append(("structural recompiled", None))
            else:
                print(f"  recompiled -> {len(skill2.steps)} steps")
                rows.append(("structural recompiled", _replay_on(page, skill2, "move_dispute_to_cases", "recompiled")))
        browser.close()

    print("\n=== FUSED SELF-HEAL SUMMARY ===")
    for name, r in rows:
        if r is None:
            print(f"  {name:<30} (no skill)")
        else:
            print(f"  {name:<30} CU={r['cu_calls']:<3} verified={r['verified']}")


if __name__ == "__main__":
    main()
