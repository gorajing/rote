"""The lowering compiler — turns a checker-verified Trajectory into a FusedSkill whose every
Step rides the CHEAPEST primitive that reliably reproduces its intent:

    typing            -> keyboard "type"
    a single key      -> keyboard "key"
    a shortcut chord  -> keyboard "hotkey"
    an app launch     -> keyboard "open_app"
    a spatial click   -> spatial "click" + a visual Precondition (crop around the target), so
                         replay re-localizes with a cheap template match — 0 model calls

Surface-aware: for surface=="desktop" we MAY run Shah's macro_compiler (a Gemini pass that
distills the whole trajectory into a keyboard-first macro) and translate its ops into Steps,
falling back to per-step lowering if that pass is unavailable; for surface=="browser" we lower
mostly to crop-gated spatial clicks plus keyboard for typing (reusing skill_compiler's crop
extraction). The passed-in `verify` spec and `params` ride straight into the FusedSkill.

Success-gated: the caller is expected to pass a trajectory the deterministic checker marked
successful — compiling an unverified one raises ValueError.

    python -m app.fusion.compiler        # smoke: lower the browser fixture, print step primitives
"""
from __future__ import annotations

import os
import sys

from .contract import FusedSkill, Precondition, Step
from ..macro_compiler import compile_macro
from ..skill_compiler import _crop_b64
from ..trace import load_trajectory

# browser-only / no-op actions: the FusedSkill.target carries the entry URL, so these are dropped
_NAV_OPS = ("open_web_browser", "take_screenshot", "navigate", "go_back", "go_forward", "search")
_MACRO_OPS = ("open_app", "hotkey", "key", "type", "wait")


def _split_keys(args: dict) -> list[str]:
    """A recorded key/hotkey action -> a list of lowercased key tokens (surface-agnostic; the
    Executor localizes them at replay, e.g. ctrl->command on macOS)."""
    raw = args.get("keys") or args.get("key") or args.get("text") or ""
    parts = raw if isinstance(raw, list) else str(raw).replace("+", " ").split()
    return [str(k).strip().lower() for k in parts if str(k).strip()]


def _spatial(s, action: str) -> Step:
    """Lower a recorded spatial action (click family / drag) onto the SPATIAL tier: normalized
    0..COORD_MAX coords in args + a Precondition crop (PNG b64) around the pixel target so replay
    can re-localize it model-free."""
    args = s.args or {}
    nx, ny = args.get("x"), args.get("y")
    sargs: dict = {}
    if nx is not None and ny is not None:
        sargs["x"], sargs["y"] = int(nx), int(ny)
    n = (s.action or "").lower()
    if action == "drag":
        dx = args.get("destination_x", args.get("dest_x"))
        dy = args.get("destination_y", args.get("dest_y"))
        if dx is not None:
            sargs["destination_x"] = int(dx)
        if dy is not None:
            sargs["destination_y"] = int(dy)
    elif "double" in n:
        sargs["clicks"] = 2
    elif "right" in n:
        sargs["button"] = "right"
    crop = _crop_b64(s.screenshot_path, *s.coords) if s.coords else None
    return Step(s.intent or s.action, action, sargs, pre=Precondition(crop_b64=crop))


def _lower_step(s, surface: str) -> list[Step]:
    """Lower ONE recorded Step to 0+ FusedSkill Steps on the cheapest reliable primitive."""
    n = (s.action or "").lower()
    args = s.args or {}
    intent = s.intent or s.action

    if "open_app" in n:                                  # app launch -> keyboard open_app
        app = args.get("app") or args.get("name") or args.get("text") or ""
        return [Step(intent, "open_app", {"name": app})]
    if "wait" in n:                                      # explicit pause -> keyboard wait
        return [Step(intent, "wait", {"seconds": float(args.get("seconds", 2) or 2)})]
    if any(k in n for k in _NAV_OPS):                    # browser nav/no-op -> target carries it
        return []
    if "type" in n:                                      # typing -> keyboard type (focus first if located)
        out: list[Step] = []
        if s.coords:                                     # type_text_at clicks to focus the field
            out.append(_spatial(s, "click"))
        out.append(Step(intent, "type", {"text": args.get("text", "")}))
        if args.get("press_enter") or args.get("enter"):
            out.append(Step(intent, "key", {"key": "enter"}))
        return out
    if "key" in n or "press" in n:                       # key / key_combination / press_key
        keys = _split_keys(args)
        if not keys:
            return []
        return [Step(intent, "hotkey", {"keys": keys})] if len(keys) > 1 \
            else [Step(intent, "key", {"key": keys[0]})]
    if "scroll" in n:                                    # no scroll primitive -> cheap keyboard paging
        down = str(args.get("direction", "down")).lower() != "up"
        return [Step(intent, "key", {"key": "pagedown" if down else "pageup"})]
    if "drag" in n:
        return [_spatial(s, "drag")]
    return [_spatial(s, "click")]                        # spatial click with no keyboard equivalent


def _to_trace(trajectory) -> dict:
    """Re-shape a Trajectory into the dict macro_compiler.compile_macro expects."""
    return {
        "intent": trajectory.final_text or trajectory.task_id,
        "steps": [{"intent": s.intent, "action": s.action,
                   "args": {k: v for k, v in (s.args or {}).items() if k != "intent"}}
                  for s in trajectory.steps],
    }


def _macro_to_steps(macro: dict) -> list[Step]:
    """Translate Shah's keyboard-first macro ops into contract Steps (all keyboard tier)."""
    steps: list[Step] = []
    for op in macro.get("steps", []):
        kind, why = op.get("op"), (op.get("why") or op.get("op") or "")
        if kind == "open_app":
            steps.append(Step(why, "open_app", {"name": op.get("app", "")}))
        elif kind == "hotkey":
            steps.append(Step(why, "hotkey", {"keys": [str(k).lower() for k in op.get("keys", [])]}))
        elif kind == "key":
            steps.append(Step(why, "key", {"key": str(op.get("key", "")).lower()}))
        elif kind == "type":
            steps.append(Step(why, "type", {"text": op.get("text", "")}))
        elif kind == "wait":
            steps.append(Step(why, "wait", {"seconds": float(op.get("seconds", 2) or 2)}))
        # unknown ops are dropped — the macro schema only emits the five above
    return steps


def _infer_target(trajectory, surface: str) -> str:
    """browser: the entry URL (first step's url). desktop: the first launched app name."""
    if surface == "desktop":
        for s in trajectory.steps:
            if "open_app" in (s.action or "").lower():
                a = s.args or {}
                return a.get("app") or a.get("name") or a.get("text") or ""
        return ""
    for s in trajectory.steps:
        if getattr(s, "url", None):
            return s.url
    return ""


def compile(trajectory, surface: str = "browser", verify: dict | None = None, *,
            name: str | None = None, params: dict | None = None,
            target: str | None = None, use_macro: bool | None = None) -> FusedSkill:
    """Lower a checker-verified Trajectory into a replayable, verifiable FusedSkill.

    surface     "browser" (crop-gated clicks + keyboard) or "desktop" (Gemini macro distillation,
                with per-step lowering as the fallback).
    verify      the Verifier spec to carry through (e.g. {"kind":"checker","checker":...}).
    use_macro   None -> auto (attempt the macro pass on desktop); True/False to force.
    """
    if getattr(trajectory, "success", None) is not True:
        raise ValueError("compile is success-gated: requires a checker-verified trajectory "
                         "(trajectory.success is not True)")
    verify = dict(verify or {})
    params = dict(params or {})

    macro = None
    if surface == "desktop" and (use_macro is None or use_macro):
        try:
            macro = compile_macro(_to_trace(trajectory))
        except Exception as e:                           # no key/network -> fall back, don't crash
            print(f"[compiler] macro distillation unavailable ({type(e).__name__}: {e}); "
                  "falling back to per-step lowering", file=sys.stderr)
            macro = None

    if macro is not None:
        steps = _macro_to_steps(macro)
        params = {**(macro.get("params") or {}), **params}   # caller params win on conflict
        name = name or macro.get("name")
        target = target or macro.get("app")
    else:
        steps = []
        for s in trajectory.steps:
            steps.extend(_lower_step(s, surface))

    return FusedSkill(
        name=name or f"fused_{trajectory.task_id}",
        surface=surface,
        target=target or _infer_target(trajectory, surface),
        params=params,
        steps=steps,
        verify=verify,
    )


if __name__ == "__main__":   # python -m app.fusion.compiler
    here = os.path.dirname(__file__)
    candidates = [
        os.path.join(here, "..", "examples", "sample_trajectory.json"),       # app/examples
        os.path.join(here, "..", "..", "examples", "sample_trajectory.json"),  # repo-root examples
    ]
    path = next((p for p in candidates if os.path.exists(p)), candidates[-1])
    traj = load_trajectory(path)
    skill = compile(
        traj, surface="browser",
        verify={"kind": "checker", "checker": "dispute_workflow",
                "params": {"customer": "Acme Corp", "note": "duplicate charge"}},
    )
    print(f"FusedSkill {skill.name!r}  surface={skill.surface}  target={skill.target}")
    print(f"steps ({len(skill.steps)}):")
    for i, st in enumerate(skill.steps):
        gate = "crop" if (st.pre and st.pre.crop_b64) else ("pre" if st.pre else "-")
        print(f"  [{i}] {st.primitive:<8} {gate:<4} {st.intent}")
    print("primitives:", [st.primitive for st in skill.steps])
