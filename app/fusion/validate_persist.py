"""Self-heal PERSISTENCE on the live browser arena — the durability claim, end to end.

validate_selfheal.py proves a drift *heals* per run (escalate 1 step → re-verify). It does NOT prove
the drift is paid only ONCE. This does: it drives the real arena through three replays of a stored,
checker-verified skill —

  1) baseline replay (heal off)            → expect CU = 0, verified         (the warm skill works)
  2) corrupt one step's crop, replay heal  → expect CU ≥ 1, verified, healed (drift re-grounds + re-cuts)
     → save the healed skill to a TEMP store, reload it FROM DISK
  3) replay the reloaded skill (heal off)  → expect CU = 0, verified         (drift paid ONCE, now free)

CU 0 → 1 → 0 across a save/load boundary is the self-IMPROVEMENT signal. Uses ~1 live CU call (the
single escalation). Persists to a tempdir, never the real registry. Browser surface only (the
demo-safe path); no desktop, no cold-learn flakiness.

  python -m app.fusion.validate_persist                       # default: fused_train-settings-plan
  python -m app.fusion.validate_persist --skill fused_train-row-acme --step 1
"""
import argparse
import base64
import tempfile

import cv2
import numpy as np
import requests
from playwright.sync_api import sync_playwright

from ..config import APP_URL, VIEWPORT
from .browser_executor import BrowserExecutor
from .dispatch import replay
from .skill_store import FusionSkillStore
from .verifier import make_verifier


def _reset(variant="baseline"):
    requests.post(f"{APP_URL}/reset?variant={variant}", timeout=5)


def _noise_crop() -> str:
    """A distinctive but arena-foreign 160×90 patch — guaranteed to MISS the live UI (forces drift)."""
    rng = np.random.default_rng(2024)
    patch = rng.integers(0, 255, (90, 160, 3), dtype=np.uint8)
    return base64.b64encode(cv2.imencode(".png", patch)[1]).decode()


def _run(page, skill, label, *, heal):
    _reset(); page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
    res = replay(skill, BrowserExecutor(page), make_verifier(skill), heal=heal)
    escalated = [sr.index for sr in res["steps"] if sr.tier == "model"]
    print(f"  {label:<28} CU={res['cu_calls']}  verified={res['verified']}  "
          f"healed={res['healed']}  escalated={escalated}", flush=True)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", default="fused_train-settings-plan")
    ap.add_argument("--step", type=int, default=0, help="index of the spatial+crop step to drift")
    args = ap.parse_args()

    real = FusionSkillStore()
    skill = real.load_active(args.skill)
    if skill is None:
        raise SystemExit(f"no active skill '{args.skill}' in {real.root}")
    if not (skill.steps[args.step].pre and skill.steps[args.step].pre.crop_b64):
        raise SystemExit(f"step {args.step} of {args.skill} has no crop to drift")

    print(f"SKILL {skill.name}  ({len(skill.steps)} steps, surface={skill.surface})\n")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_context(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]}).new_page()

        print("PHASE 1  baseline (warm skill, heal off) — expect CU=0, verified:")
        r1 = _run(page, skill, "baseline", heal=False)

        print(f"\nPHASE 2  corrupt step {args.step}'s crop, replay heal=ON — expect CU≥1, healed, verified:")
        skill.steps[args.step].pre.crop_b64 = _noise_crop()        # induce drift on this one step
        r2 = _run(page, skill, f"drift+heal step {args.step}", heal=True)

        reloaded = skill
        if r2["verified"] and r2["healed"]:
            with tempfile.TemporaryDirectory() as root:            # persist OFF the real registry
                tmp = FusionSkillStore(root)
                tmp.save_promoted(skill, verified=True, cu_calls=r2["cu_calls"], reason="validate_persist")
                reloaded = tmp.load_active(skill.name)             # a fresh object, parsed from disk
            print("  → healed skill saved + reloaded from a temp store")

        print(f"\nPHASE 3  replay the RELOADED skill (heal off) — expect CU=0, verified:")
        r3 = _run(page, reloaded, "reloaded-from-disk", heal=False)
        browser.close()

    ok = (r1["cu_calls"] == 0 and r1["verified"]
          and r2["cu_calls"] >= 1 and args.step in r2["healed"] and r2["verified"]
          and r3["cu_calls"] == 0 and r3["verified"])
    print(f"\n=== PERSISTENCE {'PROVEN' if ok else 'NOT proven'}:  CU "
          f"{r1['cu_calls']} → {r2['cu_calls']} → {r3['cu_calls']}  "
          f"(drift paid {'once, then free' if ok else 'UNEXPECTED — see phases above'}) ===")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
