"""Test the fusion engine across MORE skills — generalization beyond the one hero.

For each arena task: cold Gemini CU (retry until checker-verified) -> compile a FusedSkill ->
replay through the fusion dispatch -> report CU N->0 + verified. Proves the engine handles
dispute / refund / settings workflows (3 families, 3 checkers), not just the dispute hero.
Each verified skill is PROMOTED into the fusion skill store, building a real library that
Riccardo's retrieval db can later index.

  python -m app.fusion.test_skills                 # representative set across the 3 families
  python -m app.fusion.test_skills --split heldout # the full held-out split
  python -m app.fusion.test_skills --no-store      # measure only, don't persist
"""
import argparse

import requests
from playwright.sync_api import sync_playwright

from ..config import APP_URL, VIEWPORT
from ..cu_runner import run_task
from ..tasks import SPLITS
from .. import checker
from .compiler import compile as compile_fused
from .dispatch import replay
from .browser_executor import BrowserExecutor
from .verifier import make_verifier
from .skill_store import FusionSkillStore

MAX_COLD = 4

# one+ per family — dispute (invoice_action), refund/find (row_find_act), settings_change
DEFAULT_SET = [
    "train-dispute-initech",   # invoice_action — different customer + reason than the hero
    "train-refund-globex",     # row_find_act   — refund a paid invoice
    "train-row-acme",          # row_find_act   — find unpaid Acme >$500, refund
    "train-settings-plan",     # settings_change — change the plan
    "held-settings-email",     # settings_change — change the billing email
]


def _reset(variant="baseline"):
    requests.post(f"{APP_URL}/reset?variant={variant}", timeout=5)


def _verify_spec(task):
    return {"kind": "checker", "checker": task.checker, "params": dict(task.params)}


def _run_one(page, task, store):
    cold = None
    for attempt in range(1, MAX_COLD + 1):
        _reset(); page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
        t = run_task(task, page, max_turns=30); t.success = checker.check(task)
        print(f"    cold {attempt}: {t.n_steps} steps, {'PASS' if t.success else 'FAIL'}", flush=True)
        if t.success:
            cold = t
            break
    if cold is None:
        return {"task": task.id, "family": task.family, "cold": "FAIL", "cu": "-", "verified": False}

    skill = compile_fused(cold, surface="browser", verify=_verify_spec(task), name=f"fused_{task.id}")
    _reset(); page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
    res = replay(skill, BrowserExecutor(page), make_verifier(skill))
    if res["verified"] and store is not None:
        rec = store.save_promoted(skill, verified=True, cu_calls=res["cu_calls"], reason="multi-skill test")
        print(f"    stored {skill.name} v{rec['version']}", flush=True)
    return {"task": task.id, "family": task.family, "cold": cold.n_steps,
            "cu": res["cu_calls"], "verified": res["verified"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default=None, help="train | heldout | all (else the representative set)")
    ap.add_argument("--no-store", action="store_true", help="measure only; do not persist skills")
    args = ap.parse_args()

    by_id = {t.id: t for t in SPLITS["all"]}
    tasks = SPLITS[args.split] if args.split else [by_id[i] for i in DEFAULT_SET]
    store = None if args.no_store else FusionSkillStore()

    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_context(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]}).new_page()
        for task in tasks:
            print(f"--- {task.id}  ({task.family}, checker={task.checker}) ---", flush=True)
            r = _run_one(page, task, store)
            print(f"  => cold={r['cold']}  CU={r['cu']}  verified={r['verified']}\n", flush=True)
            rows.append(r)
        browser.close()

    print("=== MORE-SKILLS SUMMARY (fusion engine across families) ===")
    print(f"  {'task':<24}{'family':<16}{'cold':>5}{'CU':>5}  verified")
    ok = 0
    for r in rows:
        print(f"  {r['task']:<24}{r['family']:<16}{str(r['cold']):>5}{str(r['cu']):>5}  {r['verified']}")
        if r["verified"] and r["cu"] == 0:
            ok += 1
    print(f"\n  {ok}/{len(rows)} skills replayed at 0 CU, checker-verified")


if __name__ == "__main__":
    main()
