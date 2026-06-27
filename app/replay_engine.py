"""Verified deterministic replay — the unfakeable "CU calls N → 0" engine.

For each cached step: re-localize the target-crop on the CURRENT screen with a cheap
non-CU template match. HIT → fire the cached action (0 CU calls). MISS (UI drift) →
escalate that ONE step to CU to re-ground (= the self-heal trigger), counted as a CU call.

The localization gate is what makes this provably NOT blind coordinate replay: every action
is conditioned on its visual precondition still being present, and re-grounds when it isn't.

Proof:  python -m app.replay_engine   (cold model run → compile → 0-CU replay, checker-verified)
"""
import base64
import cv2
import numpy as np

from .config import VIEWPORT
from .executor import execute_action

MATCH_THRESHOLD = 0.72   # TM_CCOEFF_NORMED; below this = UI drift → escalate to CU


def _decode(b64: str):
    return cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR)


def _localize(page, crop_b64: str):
    """Return (cx, cy, score) of the crop on the current screen."""
    shot = cv2.imdecode(np.frombuffer(page.screenshot(type="png"), np.uint8), cv2.IMREAD_COLOR)
    crop = _decode(crop_b64)
    res = cv2.matchTemplate(shot, crop, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    ch, cw = crop.shape[:2]
    return loc[0] + cw // 2, loc[1] + ch // 2, float(score)


def _escalate_step_to_cu(page, step) -> None:
    """One CU call: ask the model to perform just this step on the current screen."""
    from .cu_runner import _client_lazy, _MODEL, _TOOL
    interaction = _client_lazy().interactions.create(
        model=_MODEL,
        input=[
            {"type": "text", "text": f"Perform exactly one action to accomplish: {step['intent']}"},
            {"type": "image", "data": base64.b64encode(page.screenshot(type="png")).decode(),
             "mime_type": "image/png"},
        ],
        tools=_TOOL,
    )
    for s in interaction.steps:
        if s.type == "function_call":
            execute_action(page, s.name, dict(s.arguments))
            return


def replay_skill(skill, page) -> dict:
    """Replay a compiled skill on `page`. Returns {cu_calls, steps, escalations}."""
    cu_calls = 0
    escalations = []
    for i, step in enumerate(skill.steps):
        if step.get("crop_b64"):
            cx, cy, score = _localize(page, step["crop_b64"])
            if score >= MATCH_THRESHOLD:
                args = dict(step["args"])                      # 0-CU: cached action at re-localized point
                args["x"] = round(cx / VIEWPORT[0] * 1000)
                args["y"] = round(cy / VIEWPORT[1] * 1000)
                execute_action(page, step["action"], args)
            else:                                              # MISS → UI drift → escalate to CU
                cu_calls += 1
                escalations.append({"step": i, "score": round(score, 3), "intent": step["intent"]})
                _escalate_step_to_cu(page, step)
        else:
            execute_action(page, step["action"], dict(step["args"]))   # type/navigate/etc.
    return {"cu_calls": cu_calls, "steps": len(skill.steps), "escalations": escalations}


if __name__ == "__main__":
    import requests
    from playwright.sync_api import sync_playwright
    from .schemas import Task
    from .cu_runner import run_task
    from .skill_compiler import compile_skill
    from . import checker
    from .config import APP_URL

    task = Task(id="replay-proof", site="billing",
                intent="Find the unpaid invoice from Acme Corp, mark it disputed, "
                       "add the note 'duplicate charge', then export the receipt.",
                params={"customer": "Acme Corp", "note": "duplicate charge"},
                checker="dispute_workflow", family="invoice_action")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_context(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]}).new_page()

        # 1) COLD — the model (teacher) does the task
        requests.post(f"{APP_URL}/reset?variant=baseline", timeout=5)
        page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
        cold = run_task(task, page)
        cold.success = checker.check(task)
        cold_calls = cold.n_steps                              # ~one CU call per action
        print(f"COLD: ~{cold_calls} CU calls, checker={'PASS' if cold.success else 'FAIL'}")

        # 2) COMPILE — success-gated, cache action + crop per step
        skill = compile_skill(cold)
        crops = sum(1 for s in skill.steps if s.get("crop_b64"))
        print(f"COMPILED: {len(skill.steps)} steps, {crops} cached crops")

        # 3) REPLAY — deterministic, 0 CU calls (the amortization)
        requests.post(f"{APP_URL}/reset?variant=baseline", timeout=5)
        page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
        res = replay_skill(skill, page)
        replay_ok = checker.check(task)
        browser.close()

    print("\n=== VERIFIED DETERMINISTIC REPLAY ===")
    print(f"CU calls: {cold_calls} -> {res['cu_calls']}   (escalations: {len(res['escalations'])})")
    print(f"replay checker: {'PASS' if replay_ok else 'FAIL'}")
