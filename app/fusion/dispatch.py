"""Surface-agnostic tiered replay — the dispatcher (the heart of fusion).

Replays a FusedSkill through the Executor + Verifier protocols (contract.py), routing each
step to the CHEAPEST tier that can achieve its intent:

    keyboard  -> fire blind, ZERO perception (Shah's floor). An OPTIONAL crop pre-gate first
                 confirms we're on the right screen before firing a blind shortcut (fixes
                 open-loop blindness); a gate MISS self-heals instead of firing wrong.
    spatial   -> crop-localize the step's visual precondition on the LIVE screen, ZERO model.
                 HIT fires the click at the re-grounded target; MISS escalates ONE step to CU.
    model     -> escalate ONE step to Gemini CU.

This is NOT blind coordinate replay: every spatial action is conditioned on its visual
precondition still being present and re-grounds (a single CU call) when it isn't. Success is
decided by the Verifier reading GROUND TRUTH — never self-reported.

The dispatcher NEVER imports Playwright or pyautogui; it speaks only the Executor/Verifier
protocols, so the same routing serves both the browser and desktop surfaces. The crop-match
logic mirrors app.replay_engine (which is page-coupled), reimplemented over screenshot bytes.
"""
from __future__ import annotations

import base64

import cv2
import numpy as np

from .contract import (COORD_MAX, KEYBOARD_OPS, SPATIAL_OPS, Executor, FusedSkill,
                       Step, StepResult, Verifier)

MATCH_THRESHOLD = 0.72  # TM_CCOEFF_NORMED; below this = UI drift / wrong screen -> escalate to CU


def _decode(b64: str):
    return cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR)


def _ok(result) -> bool:
    """Read an executor result forgivingly. Contract executors return {'ok': bool, ...}; tolerate
    the legacy {} / {'error': ...} shape too so a failure is never silently read as success."""
    if not isinstance(result, dict):
        return bool(result)
    if "ok" in result:
        return bool(result["ok"])
    return "error" not in result


def _tier_for(primitive: str) -> str:
    if primitive in KEYBOARD_OPS:
        return "keyboard"
    if primitive in SPATIAL_OPS:
        return "crop"
    return "model"


def _localize(shot_bytes: bytes, crop_b64: str):
    """Best non-CU template match of `crop_b64` on the current screen. Returns the matched target
    as NORMALIZED 0..COORD_MAX coords plus the match score (0..1). On any decode/shape error
    returns score 0.0 (-> treated as a MISS, i.e. escalate) rather than raising."""
    try:
        shot = cv2.imdecode(np.frombuffer(shot_bytes, np.uint8), cv2.IMREAD_COLOR)
        crop = _decode(crop_b64)
        res = cv2.matchTemplate(shot, crop, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        ch, cw = crop.shape[:2]
        h, w = shot.shape[:2]
        nx = max(0, min(COORD_MAX, round((loc[0] + cw // 2) / w * COORD_MAX)))
        ny = max(0, min(COORD_MAX, round((loc[1] + ch // 2) / h * COORD_MAX)))
        return nx, ny, float(score)
    except Exception:
        return 0, 0, 0.0


CROP_W, CROP_H = 160, 90   # healed-crop box (matches app.skill_compiler's target-crop size)


def _extract_crop(shot_bytes: bytes, nx: int, ny: int):
    """Re-cut a fresh target crop centred on NORMALIZED coords from a screenshot -> b64 PNG. After a
    crop MISS escalates to CU and the model relocates the target, this captures the crop at the
    model's coords so the NEXT replay matches it at 0 CU — the durable half of self-heal. None on
    any decode/shape error (-> heal simply not persisted, never raises)."""
    try:
        shot = cv2.imdecode(np.frombuffer(shot_bytes, np.uint8), cv2.IMREAD_COLOR)
        h, w = shot.shape[:2]
        cx, cy = int(nx / COORD_MAX * w), int(ny / COORD_MAX * h)
        x0 = max(0, min(cx - CROP_W // 2, w - CROP_W))
        y0 = max(0, min(cy - CROP_H // 2, h - CROP_H))
        crop = shot[y0:y0 + CROP_H, x0:x0 + CROP_W]
        ok, buf = cv2.imencode(".png", crop)
        return base64.b64encode(buf).decode() if ok else None
    except Exception:
        return None


def _is_distinctive(crop_b64: str) -> bool:
    """A crop with real texture (pixel variance) re-localizes to a UNIQUE spot; a flat/low-texture
    crop matches almost anywhere and, if baked in, would HIT the wrong place forever (self-heal could
    never recover it). So only a heal crop carrying enough signal to re-find itself is persisted."""
    try:
        return float(_decode(crop_b64).std()) >= 12.0   # flat UI regions sit near 0; real targets >> this
    except Exception:
        return False


# Gemini CU action names -> the two executor primitives. The contract narrows every surface to
# click/drag (spatial) + open_app/hotkey/key/type/wait (keyboard); when self-healing a single
# step we fold the model's richer action space down onto that floor.
_POINTER = {"click", "left_click", "click_at", "double_click", "triple_click",
            "right_click", "middle_click", "move", "mouse_move", "hover", "hover_at",
            "mouse_down", "mouse_up"}
_DRAG = {"drag", "drag_and_drop", "click_drag"}


def _apply_cu_action(executor: Executor, fname: str, args: dict) -> dict:
    """Execute ONE Gemini CU function_call through the narrow Executor protocol (click_at /
    fire_keyboard). Forgiving: returns {'ok': False, ...} for anything the protocol can't express
    rather than raising. Coords arrive already normalized (0..COORD_MAX); the executor denorms."""
    a = dict(args)
    if fname in _POINTER and "x" in a and "y" in a:
        return executor.click_at(int(a["x"]), int(a["y"]), "click", a)
    if fname in _DRAG:
        sx, sy = a.get("start_x", a.get("x")), a.get("start_y", a.get("y"))
        if sx is None or sy is None:
            return {"ok": False, "error": f"drag missing start coords: {a}"}
        return executor.click_at(int(sx), int(sy), "drag", a)
    if fname in ("type", "type_text", "type_text_at"):
        return executor.fire_keyboard("type", a)
    if fname in ("press_key", "key", "key_press", "keypress"):
        return executor.fire_keyboard("key", {"key": a.get("key", a.get("keys"))})
    if fname in ("hotkey", "key_combination", "keyboard_shortcut"):
        return executor.fire_keyboard("hotkey", a)
    if fname in ("wait", "wait_5_seconds"):
        return executor.fire_keyboard("wait", a)
    if fname == "open_app":
        return executor.fire_keyboard("open_app", a)
    if fname in ("open_web_browser", "take_screenshot"):
        return {"ok": True}  # no-op in the CU action space
    return {"ok": False, "error": f"action not expressible via executor protocol: {fname}"}


def _escalate(executor: Executor, step: Step) -> dict:
    """Self-heal ONE step: hand the LIVE screenshot + the step's intent to Gemini CU, take the
    single action it returns, and execute it through the executor. One CU call. Surface-agnostic
    analogue of app.replay_engine._escalate_step_to_cu (no Playwright page). Forgiving: any
    failure (no key, API error, no action) returns {'ok': False, ...} and never raises."""
    try:
        from ..cu_runner import _MODEL, _TOOL, _client_lazy  # lazy: keep genai out of the import path

        shot_bytes = executor.screenshot()
        interaction = _client_lazy().interactions.create(
            model=_MODEL,
            input=[
                {"type": "text", "text": f"Perform exactly one action to accomplish: {step.intent}"},
                {"type": "image", "data": base64.b64encode(shot_bytes).decode(), "mime_type": "image/png"},
            ],
            tools=_TOOL,
        )
        for s in interaction.steps:
            if s.type == "function_call":
                args = dict(s.arguments)
                res = _apply_cu_action(executor, s.name, args)
                # carry the re-grounded pointer coords + the pre-action shot so replay() can re-cut
                # the crop and persist the heal (only consumed when heal=True AND the run verifies).
                coords = ((int(args["x"]), int(args["y"]))
                          if (s.name in _POINTER and "x" in args and "y" in args) else None)
                return {**res, "_heal_coords": coords, "_heal_shot": shot_bytes}
        return {"ok": False, "error": "model returned no action"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def replay(skill: FusedSkill, executor: Executor, verifier: Verifier, *,
           threshold: float = MATCH_THRESHOLD, on_step=None, heal: bool = False) -> dict:
    """Replay a FusedSkill surface-agnostically via the Executor + Verifier protocols.

    Per step, route by primitive to the cheapest tier (keyboard / crop / model), recording one
    StepResult. After ALL steps, the Verifier reads ground truth — success is verifier-gated,
    never self-reported. Forgiving: a failed action is recorded (ok=False) and replay continues;
    this function never raises.

    If `on_step(i, total, cu_calls, StepResult)` is given it is called after each step — a live
    hook for a HUD to narrate the running CU-call count and per-step tier.

    heal=True (opt-in; default False keeps behaviour identical): when a spatial step's crop MISSES
    and the one-step CU escalation re-grounds the target, the step's crop is re-cut at the new
    location — but APPLIED to the skill (in place) only if the whole run re-VERIFIES, so a heal that
    didn't achieve the goal never poisons the stored skill. The caller persists a new version to
    make the next replay match at 0 CU (durable self-improvement).

    Returns {"cu_calls": int, "steps": [StepResult, ...], "verified": bool,
             "needs_recompile": bool, "healed": [int]}.
    """
    results: list[StepResult] = []
    cu_calls = 0
    pending_heals: dict[int, str] = {}

    def _emit(sr: StepResult):
        results.append(sr)
        if on_step:
            try:
                on_step(i, len(skill.steps), cu_calls, sr)
            except Exception:
                pass

    for i, step in enumerate(skill.steps):
        primitive = step.primitive
        pre = step.pre
        crop = pre.crop_b64 if pre else None
        try:
            if pre and pre.settle:
                executor.settle()  # cheap, token-free: wait for the screen to stop changing

            if primitive in KEYBOARD_OPS:
                # Shah's zero-perception floor: fire the shortcut blind. If a crop pre-gate is
                # present it must confirm we're on the right screen first — a gate MISS means the
                # blind shortcut would land wrong, so escalate (self-heal) instead of firing.
                if crop:
                    _, _, score = _localize(executor.screenshot(), crop)
                    if score < threshold:
                        res = _escalate(executor, step)
                        cu_calls += 1
                        _emit(StepResult(index=i, primitive=primitive, tier="model",
                                                  cu_calls=1, score=score, ok=_ok(res)))
                        continue
                    out = executor.fire_keyboard(primitive, step.args)
                    _emit(StepResult(index=i, primitive=primitive, tier="keyboard",
                                              cu_calls=0, score=score, ok=_ok(out)))
                else:
                    out = executor.fire_keyboard(primitive, step.args)
                    _emit(StepResult(index=i, primitive=primitive, tier="keyboard",
                                              cu_calls=0, ok=_ok(out)))

            elif primitive in SPATIAL_OPS:
                # Crop-localize the visual precondition on the LIVE screen (0 model). HIT -> click
                # the re-grounded target; MISS (drift) or no precondition -> escalate one step.
                if crop:
                    nx, ny, score = _localize(executor.screenshot(), crop)
                    if score >= threshold:
                        out = executor.click_at(nx, ny, primitive, step.args)
                        _emit(StepResult(index=i, primitive=primitive, tier="crop",
                                                  cu_calls=0, score=score, ok=_ok(out)))
                    else:
                        res = _escalate(executor, step)
                        cu_calls += 1
                        if heal and _ok(res) and res.get("_heal_coords") and step.pre is not None:
                            fresh = _extract_crop(res["_heal_shot"], *res["_heal_coords"])
                            if fresh and _is_distinctive(fresh):   # don't bake a flat 'hit-anywhere' crop
                                pending_heals[i] = fresh           # applied later iff the whole run verifies
                        _emit(StepResult(index=i, primitive=primitive, tier="model",
                                                  cu_calls=1, score=score, ok=_ok(res)))
                else:
                    res = _escalate(executor, step)
                    cu_calls += 1
                    _emit(StepResult(index=i, primitive=primitive, tier="model",
                                              cu_calls=1, ok=_ok(res)))

            else:
                # MODEL_OP (or any unlowered primitive): last resort, one CU call.
                res = _escalate(executor, step)
                cu_calls += 1
                _emit(StepResult(index=i, primitive=primitive, tier="model",
                                          cu_calls=1, ok=_ok(res)))
        except Exception:
            # Forgiving spine: never raise on a failed action. Record it and move on; the Verifier
            # still has the final say on whether the skill's goal was actually achieved.
            _emit(StepResult(index=i, primitive=primitive,
                                      tier=_tier_for(primitive), ok=False))

    # Ground-truth gate. Fail CLOSED: an unverifiable run is not a success.
    try:
        verified = bool(verifier.check(skill))
    except Exception:
        verified = False

    # Persist the self-heal ONLY when the whole run re-VERIFIED — a re-ground that didn't achieve
    # the goal must never poison the stored skill. The caller saves a new version from these crops.
    healed: list[int] = []
    if heal and verified and pending_heals:
        for idx, fresh in pending_heals.items():
            if skill.steps[idx].pre is not None:
                skill.steps[idx].pre.crop_b64 = fresh
        healed = sorted(pending_heals)

    return {
        "cu_calls": cu_calls,
        "steps": results,
        "verified": verified,
        "needs_recompile": not verified,
        "healed": healed,
    }
