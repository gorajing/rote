"""A real Dynamic-Island-style overlay at the MacBook notch, built with AppKit via PyObjC.

Unlike Tkinter, an AppKit NSWindow can sit ABOVE the menu bar, be transparent, have rounded
corners, ignore mouse events (so it never steals focus from the app being automated), and tuck
right under the physical notch. The replay runs on a worker thread and pushes status into STATE;
an NSTimer on the main thread animates the spinner and redraws — so it always looks alive.
"""
import time
import math
import threading

import objc
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory, NSWindow, NSView, NSColor,
    NSBezierPath, NSFont, NSScreen, NSTimer, NSAttributedString, NSMakeRect, NSMakePoint,
    NSWindowStyleMaskBorderless, NSBackingStoreBuffered, NSStatusWindowLevel,
    NSFontAttributeName, NSForegroundColorAttributeName,
    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorStationary,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
)

W, H = 380.0, 44.0                      # island size (points)
STATE = {"i": 0, "total": 1, "text": "Starting…", "frame": 0, "done": False}


def _notch_center(screen):
    try:
        l = screen.auxiliaryTopLeftArea().size.width
        r = screen.auxiliaryTopRightArea().origin.x
        if r > l > 0:
            return (l + r) / 2.0
    except Exception:
        pass
    return screen.frame().size.width / 2.0


def _text(s, x, y, size, bold, color):
    font = NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
    NSAttributedString.alloc().initWithString_attributes_(
        s, {NSFontAttributeName: font, NSForegroundColorAttributeName: color}
    ).drawAtPoint_(NSMakePoint(x, y))


class IslandView(NSView):
    def drawRect_(self, rect):
        b = self.bounds()
        w, h = b.size.width, b.size.height
        st = STATE
        done = st["done"]
        accent = (NSColor.colorWithCalibratedRed_green_blue_alpha_(0.18, 0.82, 0.36, 1.0) if done
                  else NSColor.colorWithCalibratedRed_green_blue_alpha_(0.04, 0.52, 1.0, 1.0))

        # the black pill
        pill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, h / 2.0, h / 2.0)
        NSColor.colorWithCalibratedWhite_alpha_(0.04, 0.97).set()
        pill.fill()

        # left indicator: rotating arc (or a check when done)
        cx, cy, r = 26.0, h / 2.0, 8.0
        accent.set()
        if done:
            chk = NSBezierPath.bezierPath(); chk.setLineWidth_(2.6)
            chk.moveToPoint_(NSMakePoint(cx - 5, cy)); chk.lineToPoint_(NSMakePoint(cx - 1, cy - 4))
            chk.lineToPoint_(NSMakePoint(cx + 6, cy + 5)); chk.stroke()
        else:
            ang = (st["frame"] * 28) % 360
            arc = NSBezierPath.bezierPath(); arc.setLineWidth_(2.6)
            arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
                NSMakePoint(cx, cy), r, ang, ang + 270)
            arc.stroke()

        # two lines of text
        msg = st["text"]
        msg = (msg[:34] + "…") if len(msg) > 35 else msg
        _text(msg, 46, h / 2.0 - 1, 12.5, True, NSColor.whiteColor())
        head = "complete" if done else f"step {st['i']} of {st['total']}"
        _text(head, 46, h / 2.0 - 15, 9.5, False, NSColor.colorWithCalibratedWhite_alpha_(0.55, 1.0))

        # progress bar on the right
        bx1, bx2, by = w - 120, w - 22, h / 2.0 - 1
        NSColor.colorWithCalibratedWhite_alpha_(0.22, 1.0).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(bx1, by, bx2 - bx1, 4), 2, 2).fill()
        frac = max(0.03, min(1.0, st["i"] / max(1, st["total"])))
        accent.set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(bx1, by, (bx2 - bx1) * frac, 4), 2, 2).fill()


class NotchIsland:
    """Controller. .step()/​.finish() are called from the worker thread (set plain dict only)."""

    def step(self, i, total, text):
        STATE.update(i=i, total=total, text=text)

    def status(self, text):
        STATE.update(text=text)

    def finish(self, text="Done"):
        STATE.update(text=text, done=True, i=STATE["total"])
        self._done_at = time.time()

    def run(self, target):
        self._done_at = None
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)   # no dock icon, no focus steal
        screen = NSScreen.mainScreen()
        sf = screen.frame()
        cx = _notch_center(screen)
        x = cx - W / 2.0
        y = sf.size.height - 32.0 - H + 6.0          # tuck up under the 32pt notch
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, W, H), NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        win.setLevel_(NSStatusWindowLevel)            # above the menu bar
        win.setCollectionBehavior_(                   # follow the user onto EVERY Space + fullscreen
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary)
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setIgnoresMouseEvents_(True)
        win.setHasShadow_(True)
        view = IslandView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        win.setContentView_(view)
        win.orderFrontRegardless()
        self._win, self._view = win, view
        print(f"island window @ x={x:.0f} y={y:.0f} {W:.0f}x{H:.0f} level={win.level()}", flush=True)

        def _tick(timer):
            STATE["frame"] += 1
            view.setNeedsDisplay_(True)
            if self._done_at and time.time() - self._done_at > 1.6:
                NSApplication.sharedApplication().terminate_(None)
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.08, True, _tick)

        def _wrap():
            try:
                target()
            finally:
                if not STATE["done"]:
                    self.finish()
        threading.Thread(target=_wrap, daemon=True).start()
        app.run()
