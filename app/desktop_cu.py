"""Desktop computer-use driver — drives the REAL macOS desktop with Gemini 3.5 CU.

Rote's main loop (app/cu_runner.py) controls a *browser* via Playwright. This module is the
desktop analogue: Gemini sees screenshots of the actual screen and we execute its actions with
pyautogui (global mouse + keyboard). Same Interactions API, same intent-capture trajectory —
only the executor changes (Playwright page -> the whole desktop).

Requires macOS permissions for the app running this process (Terminal/iTerm/VS Code):
  System Settings -> Privacy & Security -> Screen Recording  (so screenshots aren't black)
  System Settings -> Privacy & Security -> Accessibility     (so synthetic clicks/keys land)

Usage:
  python -m app.desktop_cu --probe          # check permissions only, no agent
  python -m app.desktop_cu --intent "..."   # drive the desktop toward a goal
"""
import io
import os
import sys
import json
import time
import base64
import argparse
import subprocess

import pyautogui
from PIL import Image
from google import genai

from .config import CU_MODEL, LEGACY_CU_MODEL, USE_LEGACY_CU, MAX_TURNS, STUCK_AFTER

pyautogui.FAILSAFE = True          # slam mouse to a corner to abort
pyautogui.PAUSE = 0.4              # settle time between synthetic events

_MODEL = LEGACY_CU_MODEL if USE_LEGACY_CU else CU_MODEL
# prompt-injection detection is browser-oriented and false-positives on arbitrary desktop
# screenshots (it hard-blocks the request), so it is disabled for desktop control.
_TOOL = [{"type": "computer_use", "environment": "browser",
          "enable_prompt_injection_detection": False}]

# macOS shortcuts are Command-based; the model often emits the generic "ctrl".
_KEYMAP = {"ctrl": "command", "control": "command", "cmd": "command",
           "super": "command", "win": "command", "meta": "command",
           "enter": "return", "esc": "escape", "del": "delete"}


def _logical_size():
    return pyautogui.size()  # logical points on Retina; pyautogui clicks use these


def grab_screen():
    """Screenshot the desktop, downscaled to logical size so 0-999 coords map 1:1 to clicks."""
    w, h = _logical_size()
    img = pyautogui.screenshot().resize((w, h))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8"), img


def _denorm(args):
    w, h = _logical_size()
    x, y = args.get("x"), args.get("y")
    if x is None or y is None:
        return None
    return int(x / 1000 * w), int(y / 1000 * h)


def _keys(args):
    raw = args.get("keys") or args.get("key") or args.get("text") or ""
    parts = raw if isinstance(raw, list) else str(raw).replace("+", " ").split()
    return [_KEYMAP.get(k.lower().strip(), k.lower().strip()) for k in parts if k.strip()]


def _app_ready(app: str) -> bool:
    """True once `app` has a running process AND at least one real window (i.e. usable)."""
    try:
        r = subprocess.run(
            ["osascript", "-e",
             f'tell application "System Events" to tell (first process whose name is "{app}") '
             'to return (count of windows)'],
            capture_output=True, text=True, timeout=3)
        return r.returncode == 0 and r.stdout.strip().isdigit() and int(r.stdout.strip()) > 0
    except Exception:
        return False


def ensure_app(app: str, max_wait: float = 6.0) -> str:
    """DYNAMIC guardrail: focus/launch an app and continue the INSTANT it is ready, not after a
    fixed sleep. Already-open app -> returns in ~0.3s. Cold launch -> returns as soon as its
    window appears (often 2-3s), capped at max_wait. Never a wasted full 6s when it isn't needed."""
    t0 = time.time()
    already = _app_ready(app)
    subprocess.run(["open", "-a", app], check=False)        # focus if open, launch if not
    while time.time() - t0 < max_wait:
        if _app_ready(app):
            time.sleep(0.3)                                 # let it take keyboard focus
            el = time.time() - t0
            return f"{app}: {'already open' if already else 'launched'} -> ready in {el:.1f}s"
        time.sleep(0.2)
    return f"{app}: not ready after {max_wait}s cap (continuing anyway)"


def _thumb():
    """Tiny grayscale screenshot for cheap, local, token-free screen-change detection."""
    return pyautogui.screenshot().convert("L").resize((48, 30))


def _diff(a, b) -> float:
    pa, pb = a.tobytes(), b.tobytes()                       # 1 byte/pixel (L mode); no deprecation
    return sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa)


def settle(max_wait: float = 3.0, stable_for: float = 0.5, interval: float = 0.2,
           thresh: float = 3.0) -> float:
    """DYNAMIC wait: poll the screen locally and return as soon as it has stopped changing for
    `stable_for` seconds (UI finished rendering), capped at `max_wait`. Fast when nothing is
    loading; only waits the full cap if the screen keeps changing. No model, no tokens."""
    t0 = time.time()
    prev = _thumb()
    stable_since = None
    while time.time() - t0 < max_wait:
        time.sleep(interval)
        cur = _thumb()
        if _diff(prev, cur) < thresh:                       # screen is quiet
            stable_since = stable_since or time.time()
            if time.time() - stable_since >= stable_for:
                return time.time() - t0
        else:
            stable_since = None                             # still changing -> reset
        prev = cur
    return time.time() - t0


def execute(fname: str, args: dict) -> dict:
    """Run one Gemini function_call against the macOS desktop. Forgiving: never raises."""
    n = fname.lower()
    try:
        pt = _denorm(args)
        if any(k in n for k in ("open_web_browser", "take_screenshot", "navigate",
                                "go_back", "go_forward", "search", "wait_5")):
            if "wait" in n:
                time.sleep(3)
            return {"note": f"{fname} is browser-only; no-op on desktop"}
        if "open_app" in n:
            app = args.get("app") or args.get("name") or args.get("text") or ""
            return {"opened": ensure_app(app, float(args.get("launch_wait", 6)))}
        if "wait" in n:
            time.sleep(min(float(args.get("seconds", 2)), 5)); return {}
        if "scroll" in n:
            dy = int(args.get("magnitude", args.get("amount", 300)))
            if str(args.get("direction", "down")).lower() == "down":
                dy = -dy
            (pyautogui.moveTo(*pt) if pt else None); pyautogui.scroll(dy); return {}
        if "key" in n:                                   # key_combination / press_key
            ks = _keys(args)
            if ks:
                pyautogui.hotkey(*ks) if len(ks) > 1 else pyautogui.press(ks[0])
            return {"keys": ks}
        if "type" in n:                                  # type_text_at / type
            if pt:
                pyautogui.click(*pt)
            txt = args.get("text", "")
            pyautogui.write(txt, interval=0.02)
            if args.get("press_enter") or args.get("enter"):
                pyautogui.press("return")
            return {"typed": len(txt)}
        if "drag" in n:
            if pt:
                pyautogui.moveTo(*pt); pyautogui.dragTo(int(args.get("destination_x", args["x"]) / 1000 * _logical_size()[0]),
                                                        int(args.get("destination_y", args["y"]) / 1000 * _logical_size()[1]), duration=0.4)
            return {}
        if pt:                                           # click family / move / hover
            if "double" in n:
                pyautogui.doubleClick(*pt)
            elif "right" in n:
                pyautogui.click(*pt, button="right")
            elif "move" in n or "hover" in n:
                pyautogui.moveTo(*pt)
            else:
                pyautogui.click(*pt)
            return {}
        return {"note": f"unhandled action {fname}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _unique_desktop_name(base: str, ext: str = ".docx") -> str:
    """Avoid overwriting: if base.docx exists on the Desktop, bump to base_2, base_3, ..."""
    desk = os.path.expanduser("~/Desktop")
    if not os.path.exists(os.path.join(desk, base + ext)):
        return base
    i = 2
    while os.path.exists(os.path.join(desk, f"{base}_{i}{ext}")):
        i += 1
    return f"{base}_{i}"


def _uniquify_filename(steps: list) -> list:
    """The save filename is the first `type` step after Command+S. If that name already exists,
    rewrite it to a unique one so the replay never stalls on a 'replace existing file?' dialog."""
    try:
        s_idx = next(i for i, s in enumerate(steps)
                     if s.get("op") == "hotkey" and {k.lower() for k in s.get("keys", [])} == {"command", "s"})
        f_idx = next(i for i, s in enumerate(steps) if i > s_idx and s.get("op") == "type")
    except StopIteration:
        return steps
    base = str(steps[f_idx].get("text", "")).strip()
    if not base:
        return steps
    uniq = _unique_desktop_name(base)
    if uniq != base:
        steps = list(steps)
        steps[f_idx] = {**steps[f_idx], "text": uniq}
        print(f"       filename '{base}' already on Desktop -> saving as '{uniq}'")
    return steps


def replay(macro: dict, on_step=None):
    """Backward-compatible wrapper around the condition-aware, model-free replay path."""
    from .verified_replay import replay_verified

    def event(kind, payload):
        if kind == "step" and on_step:
            step = payload["step"]
            on_step(payload["index"], payload["total"], step.get("why", step["op"]))

    result = replay_verified(macro, allow_repair=False, on_event=event)
    result.update({
        "tokens": 0,
        "model_s": 0.0,
        "screenshot_s": 0.0,
        "execute_s": result["elapsed_s"],
        "model_pct": 0,
        "final": "macro replayed" if result["success"] else "macro verification failed",
    })
    print(f"\n=== metrics: {json.dumps({k: v for k, v in result.items() if k != 'failure'}, default=str)} ===")
    return result


def probe() -> bool:
    """Verify Screen Recording (non-black capture) + Accessibility (cursor actually moves)."""
    _, img = grab_screen()
    extrema = img.convert("L").getextrema()
    screen_ok = extrema[1] - extrema[0] > 10          # any contrast -> not a black frame
    w, h = _logical_size()
    pyautogui.moveTo(w // 2, h // 2, duration=0.2)
    moved = abs(pyautogui.position()[0] - w // 2) < 5  # cursor landed where we asked
    print(f"Screen Recording (capture has content): {'OK' if screen_ok else 'BLOCKED — capture is blank'}")
    print(f"Accessibility    (cursor obeyed move) : {'OK' if moved else 'BLOCKED — synthetic input ignored'}")
    return screen_ok and moved


def _tokens(interaction) -> int:
    """Best-effort total token count for one interaction (API field names vary)."""
    u = getattr(interaction, "usage", None)
    if u is None:
        return 0
    d = u.model_dump() if hasattr(u, "model_dump") else dict(u)
    for k in ("total_token_count", "total_tokens", "total"):
        if d.get(k):
            return int(d[k])
    return int(sum(v for v in d.values() if isinstance(v, (int, float))))


def run(intent: str, skill_md: str | None = None, max_turns: int | None = None,
        trace_path: str | None = None):
    """Drive the desktop toward `intent`. If `skill_md` is given, inject it as a recipe the
    model should follow (re-grounded visually). Returns metrics; if `trace_path` is given the
    full intent log (the trajectory the compiler model reads) is written there as JSON."""
    client = genai.Client()
    max_turns = max_turns or MAX_TURNS
    traj = []
    preamble = ("You are operating a macOS desktop via screenshots. The apps you need are "
                "already open — switch between them by pressing Command+Tab (hold Command, tap "
                "Tab to cycle) or by clicking their window. Do NOT use Spotlight / Command+Space.\n")
    if skill_md:
        preamble += ("\nYou have a VERIFIED step-by-step recipe for this exact task. Follow its "
                     "steps in order, re-locating each target on the CURRENT screen:\n\n"
                     + skill_md + "\n\nGOAL: ")
    t0 = time.time()
    tokens = 0
    t_shot = t_api = t_exec = 0.0          # per-phase time accumulators (the latency breakdown)

    _s = time.time(); shot_b64, _ = grab_screen(); t_shot += time.time() - _s
    _s = time.time()
    interaction = client.interactions.create(
        model=_MODEL,
        input=[{"type": "text", "text": preamble + intent},
               {"type": "image", "data": shot_b64, "mime_type": "image/png"}],
        tools=_TOOL,
    )
    t_api += time.time() - _s
    tokens += _tokens(interaction)
    recent = []
    final = ""
    steps_done = 0
    for turn in range(1, max_turns + 1):
        calls = [s for s in interaction.steps if s.type == "function_call"]
        if not calls:
            final = " ".join(c.text for s in interaction.steps if s.type == "model_output"
                             for c in s.content if c.type == "text")
            print(f"\nfinal: {final}")
            break
        responses = []
        for call in calls:
            args = dict(call.arguments)
            steps_done += 1
            _s = time.time(); result = execute(call.name, args); dt_exec = time.time() - _s
            if "safety_decision" in args:
                result["safety_acknowledgement"] = True
            _s = time.time(); shot_b64, _ = grab_screen(); dt_shot = time.time() - _s
            t_exec += dt_exec; t_shot += dt_shot
            print(f"  [{turn:>2}] {call.name:<18} exec={dt_exec:4.1f}s shot={dt_shot:4.1f}s  {args.get('intent','')}")
            traj.append({"turn": turn, "action": call.name, "intent": args.get("intent", ""), "args": args})
            responses.append({"type": "function_result", "name": call.name, "call_id": call.id,
                              "result": [{"type": "text", "text": json.dumps({"url": "desktop://macos", **result})},
                                         {"type": "image", "data": shot_b64, "mime_type": "image/png"}]})
        # stuck = same action AND same args repeated (so typing different text isn't flagged)
        recent.append(json.dumps([[c.name, str(dict(c.arguments).get("text", ""))[:24]] for c in calls]))
        if len(recent) >= STUCK_AFTER and len(set(recent[-STUCK_AFTER:])) == 1:
            final = "ABORTED: stuck (no progress)"; print("\n" + final); break
        _s = time.time()
        interaction = client.interactions.create(
            model=_MODEL, previous_interaction_id=interaction.id, input=responses, tools=_TOOL)
        dt_api = time.time() - _s; t_api += dt_api
        print(f"       └ model inference: {dt_api:4.1f}s")
        tokens += _tokens(interaction)
    elapsed = time.time() - t0
    metrics = {"steps": steps_done, "elapsed_s": round(elapsed, 1), "tokens": tokens,
               "model_s": round(t_api, 1), "screenshot_s": round(t_shot, 1), "execute_s": round(t_exec, 1),
               "model_pct": round(100 * t_api / elapsed) if elapsed else 0,
               "used_skill": bool(skill_md), "final": final}
    if trace_path:
        os.makedirs(os.path.dirname(trace_path) or ".", exist_ok=True)
        with open(trace_path, "w") as f:
            json.dump({"intent": intent, "steps": traj, "metrics": metrics}, f, indent=2, default=str)
        metrics["trace_path"] = trace_path
        print(f"    intent log saved -> {trace_path}")
    print(f"\n=== metrics: {json.dumps(metrics)} ===")
    print(f"    TIME BREAKDOWN: model={metrics['model_s']}s ({metrics['model_pct']}%)  "
          f"screenshots={metrics['screenshot_s']}s  execute={metrics['execute_s']}s")
    return metrics


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="check permissions only")
    ap.add_argument("--replay", default=None, help="path to a macro JSON to replay (no model)")
    ap.add_argument("--skill", default=None, help="path to a markdown skill recipe to inject")
    ap.add_argument("--trace", default=None, help="path to write the intent log (trajectory) JSON")
    ap.add_argument("--max-turns", type=int, default=None, help="override the per-task step cap")
    ap.add_argument("--intent", default=(
        "Create a new Microsoft Word document, type the sentence "
        "'Hello from Gemini Computer Use.' into it, then save it to the Desktop "
        "with the filename 'gemini'. Use Command+S to save; if a location picker "
        "appears choose 'On My Mac' and the Desktop folder."))
    a = ap.parse_args()

    if a.probe:
        sys.exit(0 if probe() else 1)
    if not probe():
        print("\nFix permissions first (System Settings -> Privacy & Security), then re-run.")
        sys.exit(1)
    print("\nPermissions OK. Driving the desktop (keep hands off the mouse/keyboard)...\n")
    if a.replay:
        subprocess.run(["osascript", "-e", 'tell application "Microsoft Word" to quit saving no'],
                       check=False, capture_output=True)
        time.sleep(2)
        out = os.path.expanduser("~/Desktop/gemini.docx")
        for p in (out, os.path.expanduser("~/Desktop/~$gemini.docx")):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        replay(json.load(open(a.replay)))
        print(f"\nfile created: {os.path.exists(out)}  -> {out}")
        sys.exit(0)
    skill_md = open(a.skill).read() if a.skill else None
    run(a.intent, skill_md=skill_md, max_turns=a.max_turns, trace_path=a.trace)
