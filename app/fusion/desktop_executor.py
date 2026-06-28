"""Desktop Executor — the macOS backend of the surface-agnostic Executor protocol.

Implements `contract.Executor` for surface="desktop" by delegating to Shah's desktop CU
driver (`app.desktop_cu`): the same screenshot capture/resize, the ctrl->command keymap, the
osascript app launcher, and the token-free grayscale settle()/_diff that the live agent uses.
Nothing here re-implements those — the dispatcher just gets a thin, FORGIVING wrapper that
returns result dicts and never raises on a failed action.

Coordinate space: the model emits normalized 0..COORD_MAX coords; we denormalize to macOS
logical points (the same points pyautogui clicks and that `grab_screen` resizes the capture to,
so crop-match and clicks line up) via `desktop_cu._denorm`.
"""
from __future__ import annotations

import base64
import time
from typing import Optional

import pyautogui

from app import desktop_cu
from app.fusion.contract import Surface


class DesktopExecutor:
    """pyautogui-backed Executor for the real macOS desktop.

    Surface-agnostic to the dispatcher: it speaks only the contract's screenshot / fire_keyboard
    / click_at / settle methods and keeps pyautogui + osascript behind this wall."""

    surface: Surface = "desktop"

    # ── perception ────────────────────────────────────────────────────────────────────────
    def screenshot(self) -> bytes:
        """PNG bytes of the screen at the SAME logical resolution the model sees (so normalized
        0..COORD_MAX coords map consistently). Reuses desktop_cu.grab_screen's capture+resize."""
        b64, _ = desktop_cu.grab_screen()
        return base64.b64decode(b64)

    # ── keyboard tier (0 perception, 0 model) ───────────────────────────────────────────────
    def fire_keyboard(self, op: str, args: dict) -> dict:
        """Execute a KEYBOARD_OP blind. op in ("open_app","hotkey","key","type","wait").
        Forgiving: returns {"ok": bool, ...}, never raises."""
        args = args or {}
        try:
            if op == "open_app":
                app = args.get("name") or args.get("app") or args.get("text") or ""
                if not app:
                    return {"ok": False, "error": "open_app: no app name"}
                note = desktop_cu.ensure_app(app, float(args.get("launch_wait", 6)))
                return {"ok": True, "opened": note}

            if op == "hotkey":
                ks = desktop_cu._keys(args)
                if not ks:
                    return {"ok": False, "error": "hotkey: no keys"}
                pyautogui.hotkey(*ks)
                return {"ok": True, "keys": ks}

            if op == "key":
                ks = desktop_cu._keys(args)
                if not ks:
                    return {"ok": False, "error": "key: no key"}
                # one logical key (mapped via _KEYMAP); chord-shaped input still works
                pyautogui.press(ks[0]) if len(ks) == 1 else pyautogui.hotkey(*ks)
                return {"ok": True, "key": ks[0] if len(ks) == 1 else ks}

            if op == "type":
                txt = args.get("text", "")
                pyautogui.write(txt, interval=0.02)
                if args.get("press_enter") or args.get("enter"):
                    pyautogui.press("return")
                return {"ok": True, "typed": len(txt)}

            if op == "wait":
                secs = min(float(args.get("seconds", 1.0)), 10.0)
                time.sleep(secs)
                return {"ok": True, "waited": secs}

            return {"ok": False, "error": f"unknown keyboard op {op!r}"}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ── spatial tier (cheap crop localization, 0 model) ─────────────────────────────────────
    def click_at(self, x: int, y: int, action: str = "click",
                 args: Optional[dict] = None) -> dict:
        """Click/drag at NORMALIZED 0..COORD_MAX coords, denormalized to macOS logical points.
        Forgiving: returns {"ok": bool, ...}, never raises."""
        args = args or {}
        try:
            pt = desktop_cu._denorm({"x": x, "y": y})
            if pt is None:
                return {"ok": False, "error": f"click_at: bad coords ({x},{y})"}
            px, py = pt

            if action == "drag":
                dst = self._denorm_dest(args)
                if dst is None:
                    return {"ok": False, "error": "drag: missing destination coords"}
                dx, dy = dst
                pyautogui.moveTo(px, py)
                pyautogui.dragTo(dx, dy, duration=0.4)
                return {"ok": True, "from": [px, py], "to": [dx, dy]}

            # click family — forgiving on button/double via args
            if args.get("button") == "right":
                pyautogui.click(px, py, button="right")
            elif args.get("double") or args.get("clicks") == 2:
                pyautogui.doubleClick(px, py)
            else:
                pyautogui.click(px, py)
            return {"ok": True, "x": px, "y": py}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @staticmethod
    def _denorm_dest(args: dict):
        """Denormalize a drag destination from whatever key the step carried it under."""
        for kx, ky in (("destination_x", "destination_y"), ("to_x", "to_y"),
                       ("x2", "y2"), ("dest_x", "dest_y")):
            if args.get(kx) is not None and args.get(ky) is not None:
                return desktop_cu._denorm({"x": args[kx], "y": args[ky]})
        return None

    # ── token-free settle ───────────────────────────────────────────────────────────────────
    def settle(self, timeout: float = 3.0) -> None:
        """Wait until the screen stops changing, via desktop_cu's grayscale-diff detector. No
        model, no tokens. Returns None per the contract (the elapsed float is discarded)."""
        desktop_cu.settle(max_wait=timeout)
