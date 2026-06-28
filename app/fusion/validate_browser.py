"""Live validation of the FUSED browser path on the real arena — the milestone proof.

cold (Gemini CU) -> compile (lowering) -> dispatch.replay through BrowserExecutor -> CheckerVerifier.
Proves a genuine CU N -> 0, verified against /state, on the FUSED engine (not the stub self-test).

  python -m app.fusion.validate_browser
"""
import requests
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


def _reset(variant="baseline"):
    requests.post(f"{APP_URL}/reset?variant={variant}", timeout=5)


def main():
    verify_spec = {"kind": "checker", "checker": HERO.checker, "params": dict(HERO.params)}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_context(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]}).new_page()

        # 1) COLD — retry until one checker-verified trajectory to compile from
        cold = None
        for attempt in range(1, MAX_COLD + 1):
            _reset("baseline"); page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
            t = run_task(HERO, page, max_turns=30); t.success = checker.check(HERO)
            print(f"COLD attempt {attempt}: {t.n_steps} steps, {'PASS' if t.success else 'FAIL'}", flush=True)
            if t.success:
                cold = t
                break
        if cold is None:
            browser.close(); raise SystemExit("no verified cold trajectory in %d tries" % MAX_COLD)

        # 2) COMPILE — lower into a FusedSkill (crops captured from the cold run's screenshots)
        skill = compile_fused(cold, surface="browser", verify=verify_spec)
        crops = sum(1 for s in skill.steps if s.pre and s.pre.crop_b64)
        print(f"COMPILED: {len(skill.steps)} steps {[s.primitive for s in skill.steps]}, "
              f"{crops} crops", flush=True)

        # 3) REPLAY — fused dispatcher through BrowserExecutor, verifier-gated
        _reset("baseline"); page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
        res = replay(skill, BrowserExecutor(page), make_verifier(skill))
        browser.close()

    print("\n=== FUSED BROWSER REPLAY (live, on the arena) ===")
    print(f"cold steps: {cold.n_steps}   CU calls: {cold.n_steps} -> {res['cu_calls']}")
    print(f"verified (ground truth): {res['verified']}   needs_recompile: {res['needs_recompile']}")
    for sr in res["steps"]:
        sc = f"{sr.score:.2f}" if sr.score is not None else "-"
        print(f"  [{sr.index}] {sr.primitive:<8} tier={sr.tier:<8} cu={sr.cu_calls} score={sc} ok={sr.ok}")


if __name__ == "__main__":
    main()
