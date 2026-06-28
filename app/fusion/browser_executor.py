"""BrowserExecutor — the Playwright backend of the surface-agnostic Executor protocol.

Implements contract.Executor for surface="browser". Constructed with a live Playwright
`page`; the dispatcher drives it without ever importing Playwright itself. Reuses
app.executor.execute_action (which already denormalizes 0..COORD_MAX coords against the
locked VIEWPORT and performs the Playwright click/type/scroll/drag/keys) so spatial replay
here is identical to the CU execution path.

FORGIVING: every action method returns a {"ok": bool, ...} result and never raises on a
failed action; the dispatcher decides escalation. See contract.py for the full spec.
"""
from __future__ import annotations

import time

from .contract import Surface
from ..config import VIEWPORT, denorm
from ..executor import execute_action

# macOS/pyautogui-style key names -> Playwright modifier names (Meta == ⌘).
_MOD_MAP = {
    "cmd": "Meta", "command": "Meta", "meta": "Meta", "win": "Meta", "super": "Meta",
    "ctrl": "Control", "control": "Control",
    "alt": "Alt", "option": "Alt", "opt": "Alt",
    "shift": "Shift",
}

# common named keys -> Playwright key names (Playwright is case-sensitive for named keys).
_KEY_MAP = {
    "enter": "Enter", "return": "Enter", "tab": "Tab",
    "esc": "Escape", "escape": "Escape",
    "backspace": "Backspace", "delete": "Delete", "del": "Delete",
    "space": " ", "spacebar": " ",
    "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
    "arrowup": "ArrowUp", "arrowdown": "ArrowDown",
    "arrowleft": "ArrowLeft", "arrowright": "ArrowRight",
    "home": "Home", "end": "End", "pageup": "PageUp", "pagedown": "PageDown",
}

# spatial sub-actions execute_action understands directly (everything else falls back to click).
_CLICK_FNAMES = ("click", "double_click", "triple_click", "right_click", "middle_click")


def _norm_key(k) -> str:
    """Normalize a single key token to a Playwright-acceptable name."""
    ks = str(k).strip()
    low = ks.lower()
    if low in _KEY_MAP:
        return _KEY_MAP[low]
    if low in _MOD_MAP:
        return _MOD_MAP[low]
    if len(ks) == 1:                       # single character — keep original case
        return ks
    if low.startswith("f") and low[1:].isdigit():   # function keys F1..F12
        return ks.upper()
    return ks


def _chord(keys) -> str:
    """Build a Playwright chord string (e.g. ['command','s'] -> 'Meta+s')."""
    parts = []
    for k in keys or []:
        low = str(k).strip().lower()
        parts.append(_MOD_MAP[low] if low in _MOD_MAP else _norm_key(k))
    return "+".join(parts)


def _end_coords(x: int, y: int, args: dict) -> tuple[int, int]:
    """Resolve a drag's normalized end point from a forgiving set of arg shapes."""
    to = args.get("to") or args.get("end") or args.get("dest")
    if isinstance(to, dict):
        return int(to.get("x", x)), int(to.get("y", y))
    if isinstance(to, (list, tuple)) and len(to) >= 2:
        return int(to[0]), int(to[1])
    ex = args.get("end_x", args.get("x2", args.get("to_x", args.get("dest_x", x))))
    ey = args.get("end_y", args.get("y2", args.get("to_y", args.get("dest_y", y))))
    return int(ex), int(ey)


class BrowserExecutor:
    """Playwright implementation of the contract.Executor protocol (surface='browser')."""

    surface: Surface = "browser"

    def __init__(self, page):
        self.page = page

    # ── perception ──────────────────────────────────────────────────────────────────────
    def screenshot(self) -> bytes:
        """PNG bytes of the current page at the locked VIEWPORT — the same logical resolution
        the model sees, so normalized 0..COORD_MAX coords map consistently."""
        return self.page.screenshot(type="png")

    # ── keyboard tier (0 perception, 0 model) ────────────────────────────────────────────
    def fire_keyboard(self, op: str, args: dict | None = None) -> dict:
        """Execute a KEYBOARD_OP with no perception. Forgiving: returns {'ok': bool, ...}."""
        args = args or {}
        try:
            if op == "type":
                text = args.get("text", "")
                self.page.keyboard.type(text)
                if args.get("press_enter") or args.get("enter"):
                    self.page.keyboard.press("Enter")
                return {"ok": True, "op": op, "text": text}

            if op == "key":
                key = _norm_key(args.get("key", args.get("keys", "")))
                self.page.keyboard.press(key)
                return {"ok": True, "op": op, "key": key}

            if op == "hotkey":
                keys = args.get("keys") or args.get("chord") or []
                if isinstance(keys, str):
                    keys = keys.replace("+", " ").split()
                chord = _chord(keys)
                self.page.keyboard.press(chord)
                return {"ok": True, "op": op, "chord": chord}

            if op == "wait":
                secs = float(args.get("seconds", args.get("ms", 1000) / 1000
                                       if "ms" in args else 1))
                time.sleep(secs)
                return {"ok": True, "op": op, "seconds": secs}

            if op == "open_app":
                # a browser has no apps; navigate when a URL-ish target is given, else no-op.
                cand = args.get("url") or args.get("target")
                name = args.get("name")
                if not cand and isinstance(name, str) and "://" in name:
                    cand = name
                if cand:
                    self.page.goto(cand)
                    self.settle()
                    return {"ok": True, "op": op, "navigated": cand}
                return {"ok": True, "op": op, "noop": True}

            return {"ok": False, "op": op, "error": f"unknown keyboard op: {op}"}
        except Exception as e:
            return {"ok": False, "op": op, "error": str(e)}

    # ── spatial tier (cheap vision upstream, 0 model) ────────────────────────────────────
    def click_at(self, x: int, y: int, action: str = "click", args: dict | None = None) -> dict:
        """Execute a SPATIAL_OP at NORMALIZED 0..COORD_MAX coords. execute_action denormalizes
        against VIEWPORT and performs the Playwright mouse action. Forgiving."""
        args = args or {}
        try:
            if action == "drag":
                ex, ey = _end_coords(x, y, args)
                res = execute_action(self.page, "drag_and_drop",
                                     {"start_x": x, "start_y": y, "end_x": ex, "end_y": ey})
                end_px = denorm(ex, ey)
            else:
                fname = action if action in _CLICK_FNAMES else "click"
                if fname == "click":          # the spatial primitive folds sub-actions into args
                    if args.get("clicks") == 3:
                        fname = "triple_click"
                    elif args.get("clicks") == 2 or args.get("double"):
                        fname = "double_click"
                    elif args.get("button") == "right":
                        fname = "right_click"
                res = execute_action(self.page, fname, {"x": x, "y": y})
                end_px = None

            if res.get("error"):
                return {"ok": False, "action": action, "x": x, "y": y, "error": res["error"]}

            out = {"ok": True, "action": action, "x": x, "y": y, "px": denorm(x, y)}
            if end_px is not None:
                out["end_px"] = end_px
            return out
        except Exception as e:
            return {"ok": False, "action": action, "x": x, "y": y, "error": str(e)}

    # ── settle ───────────────────────────────────────────────────────────────────────────
    def settle(self, timeout: float = 3.0) -> None:
        """Cheap, token-free wait until the page stops changing: wait for the network to go
        idle (fall back to the default load state) then a short pause. Never raises."""
        ms = int(timeout * 1000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=ms)
        except Exception:
            try:
                self.page.wait_for_load_state(timeout=ms)
            except Exception:
                pass
        time.sleep(0.4)
