"""Verified deterministic replay — the unfakeable "CU calls N → 0" engine.

For each cached step: re-localize the target-crop on the CURRENT screen with a cheap
non-CU template match. HIT → fire the cached action (0 CU calls). MISS (UI drift) →
escalate that ONE step to CU to re-ground (= the self-heal trigger), capture a fresh crop,
and PATCH the skill in place so the next replay is cheap again.

The localization gate is what makes this provably NOT blind coordinate replay: every action
is conditioned on its visual precondition still being present, and re-grounds when it isn't.

Cost-collapse proof:  python -m app.replay_engine
Self-heal proof:      python -m app.selfheal_proof
"""
import base64
import cv2
import numpy as np

from .config import VIEWPORT
from .executor import execute_action

MATCH_THRESHOLD = 0.72   # TM_CCOEFF_NORMED; below this = UI drift → escalate to CU


def _decode(b64: str):
    return cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR)


def _crop_around(img, cx: int, cy: int):
    from .skill_compiler import CROP_W, CROP_H
    h, w = img.shape[:2]
    x0 = max(0, min(cx - CROP_W // 2, w - CROP_W))
    y0 = max(0, min(cy - CROP_H // 2, h - CROP_H))
    ok, buf = cv2.imencode(".png", img[y0:y0 + CROP_H, x0:x0 + CROP_W])
    return base64.b64encode(buf).decode() if ok else None


def _localize(page, crop_b64: str):
    """Return (cx, cy, score) of the crop's best match on the current screen."""
    shot = cv2.imdecode(np.frombuffer(page.screenshot(type="png"), np.uint8), cv2.IMREAD_COLOR)
    crop = _decode(crop_b64)
    res = cv2.matchTemplate(shot, crop, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    ch, cw = crop.shape[:2]
    return loc[0] + cw // 2, loc[1] + ch // 2, float(score)


def _escalate_step_to_cu(page, step):
    """One CU call: perform this step on the current screen. Returns a fresh target-crop of
    where CU clicked (for patching), or None for non-spatial actions."""
    from .cu_runner import _client_lazy, _MODEL, _TOOL
    shot_bytes = page.screenshot(type="png")
    interaction = _client_lazy().interactions.create(
        model=_MODEL,
        input=[
            {"type": "text", "text": f"Perform exactly one action to accomplish: {step['intent']}"},
            {"type": "image", "data": base64.b64encode(shot_bytes).decode(), "mime_type": "image/png"},
        ],
        tools=_TOOL,
    )
    for s in interaction.steps:
        if s.type == "function_call":
            args = dict(s.arguments)
            new_crop = None
            if "x" in args and "y" in args:
                img = cv2.imdecode(np.frombuffer(shot_bytes, np.uint8), cv2.IMREAD_COLOR)
                cx = int(args["x"] / 1000 * VIEWPORT[0])
                cy = int(args["y"] / 1000 * VIEWPORT[1])
                new_crop = _crop_around(img, cx, cy)
            execute_action(page, s.name, args)
            return new_crop
    return None


def replay_skill(skill, page, verbose: bool = False, heal: bool = True) -> dict:
    """Replay a compiled skill on `page`. Returns {cu_calls, steps, escalations}.
    On a crop miss: escalate one step to CU and (if heal) patch the cached crop in place."""
    cu_calls = 0
    escalations = []
    for i, step in enumerate(skill.steps):
        label = (step.get("intent") or step["action"])[:48]
        if step.get("crop_b64"):
            cx, cy, score = _localize(page, step["crop_b64"])
            if score >= MATCH_THRESHOLD:
                args = dict(step["args"])
                args["x"] = round(cx / VIEWPORT[0] * 1000)
                args["y"] = round(cy / VIEWPORT[1] * 1000)
                execute_action(page, step["action"], args)
                if verbose:
                    print(f"  [{i+1}] HIT  score={score:.2f}  {label}")
            else:
                cu_calls += 1
                new_crop = _escalate_step_to_cu(page, step)
                if heal and new_crop:
                    step["crop_b64"] = new_crop
                escalations.append({"step": i, "score": round(score, 3), "intent": step["intent"]})
                if verbose:
                    print(f"  [{i+1}] MISS score={score:.2f} → CU re-ground → "
                          f"{'patched' if new_crop else 'no-patch'}  {label}")
        else:
            execute_action(page, step["action"], dict(step["args"]))
            if verbose:
                print(f"  [{i+1}] play {step['action']:<8} {label}")
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
        requests.post(f"{APP_URL}/reset?variant=baseline", timeout=5)
        page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
        cold = run_task(task, page); cold.success = checker.check(task)
        print(f"COLD: ~{cold.n_steps} CU calls, checker={'PASS' if cold.success else 'FAIL'}")
        skill = compile_skill(cold)
        print(f"COMPILED: {len(skill.steps)} steps, {sum(1 for s in skill.steps if s.get('crop_b64'))} crops")
        requests.post(f"{APP_URL}/reset?variant=baseline", timeout=5)
        page.goto(f"{APP_URL}/billing", wait_until="domcontentloaded")
        res = replay_skill(skill, page); replay_ok = checker.check(task)
        browser.close()
    print(f"\n=== VERIFIED DETERMINISTIC REPLAY ===\nCU calls: {cold.n_steps} -> {res['cu_calls']}  "
          f"(escalations: {len(res['escalations'])})\nreplay checker: {'PASS' if replay_ok else 'FAIL'}")
