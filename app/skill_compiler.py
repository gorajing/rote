"""compile_skill: turn a checker-verified Trajectory into a Skill that supports VERIFIED
DETERMINISTIC REPLAY. Each step caches its action + a small TARGET-CROP of the pre-action
screenshot (a visual precondition), alongside the semantic target_desc. On replay we
re-localize the crop with a cheap non-CU template match and fire the cached action — 0 CU
calls — escalating a single step to CU only on a miss (UI drift = the self-heal trigger).

Success-gated: only compiles from a trajectory the deterministic checker marked successful."""
import os
import base64
import cv2
import numpy as np

from .schemas import Trajectory, Skill
from .config import TRACES_DIR

CROP_W, CROP_H = 160, 90   # visual precondition box around each click target


def _resolve(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(TRACES_DIR, os.path.basename(path))


def _crop_b64(screenshot_path: str, cx: int, cy: int):
    """Crop a box centred on the click target from the pre-action screenshot → base64 PNG."""
    img = cv2.imread(_resolve(screenshot_path))
    if img is None:
        return None
    h, w = img.shape[:2]
    x0 = max(0, min(cx - CROP_W // 2, w - CROP_W))
    y0 = max(0, min(cy - CROP_H // 2, h - CROP_H))
    crop = img[y0:y0 + CROP_H, x0:x0 + CROP_W]
    ok, buf = cv2.imencode(".png", crop)
    return base64.b64encode(buf).decode() if ok else None


def compile_skill(traj: Trajectory, name: str | None = None, site: str = "billing") -> Skill:
    if traj.success is not True:
        raise ValueError("compile_skill is success-gated: requires a checker-verified trajectory")
    steps = []
    for s in traj.steps:
        step = {
            "action": s.action,
            "intent": s.intent,
            "target_desc": s.intent,
            "args": {k: v for k, v in s.args.items() if k != "intent"},
            "coords": list(s.coords) if s.coords else None,
            "crop_b64": _crop_b64(s.screenshot_path, *s.coords) if s.coords else None,
        }
        steps.append(step)
    return Skill(
        name=name or f"skill_{traj.task_id}", site=site,
        goal_template="(compiled from trajectory)", params=[],
        preconditions={}, steps=steps, success_checks=[],
    )
