"""The Trajectory recorder — the SPINE of Rote. Every CU step is captured as an annotated
pixel action (intent + action + coords + screenshot). Observation, eval, judging, and
improvement are all pure functions of this trace. The `intent` field (Gemini 3.5 only) is
what turns raw coordinates into a semantic, re-groundable record."""
import os
import base64
import hashlib
from .schemas import Step, Trajectory
from .config import VIEWPORT


def screenshot_b64(page) -> str:
    return base64.b64encode(page.screenshot(type="png")).decode("utf-8")


def save_screenshot(page, out_dir: str, task_id: str, turn: int) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{task_id}_t{turn}.png")
    page.screenshot(path=path, type="png")
    return path


def state_hash(page) -> str:
    """Screenshot bytes + URL — used by the circuit breaker to detect 'stuck' (no progress)."""
    return hashlib.md5(page.screenshot(type="png") + page.url.encode()).hexdigest()


def record_step(traj: Trajectory, turn: int, fname: str, args: dict, page, screenshot_path: str):
    """Append one annotated step to the trajectory. coords are the denormalized pixel target
    (None for non-spatial actions like type/navigate)."""
    x, y = args.get("x"), args.get("y")
    coords = (int(x / 1000 * VIEWPORT[0]), int(y / 1000 * VIEWPORT[1])) if x is not None and y is not None else None
    traj.steps.append(Step(
        turn=turn,
        intent=args.get("intent", ""),     # the Gemini-3.5 per-step reasoning — the keystone field
        action=fname,
        args=dict(args),
        coords=coords,
        screenshot_path=screenshot_path,
        url=page.url,
    ))
