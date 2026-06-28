"""A Dynamic-Island-style overlay that MERGES with the MacBook notch (AppKit / PyObjC).

Key to looking native: the panel is BLACK like the notch (a subtle black->near-black gradient),
not a grey frosted panel — so its flush top blends seamlessly into the physical notch and it reads
as the notch growing downward, not a separate card. No rectangular window shadow, no stray border.

Motion follows Emil Kowalski's principles (Dynamic Island = the canonical spring component):
  - spring entrance scaling from the TOP (out of the notch) + opacity, strong ease-out
  - staggered inner content, faster spinner, subtle completion pop, snappier exit
  - honors prefers-reduced-motion

Joins all Spaces, sits above the menu bar, ignores mouse events (never steals focus).
"""
import time
import threading

from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory, NSWindow, NSView, NSColor, NSFont,
    NSScreen, NSWorkspace, NSTimer, NSMakeRect, NSWindowStyleMaskBorderless, NSBackingStoreBuffered,
    NSStatusWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary, NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSAnimationContext,
)
from Quartz import (
    CAShapeLayer, CAGradientLayer, CATextLayer, CABasicAnimation, CASpringAnimation, CATransaction,
    CAMediaTimingFunction, CACurrentMediaTime, CGRectMake,
    CGPathCreateWithEllipseInRect, CGPathCreateMutable, CGPathMoveToPoint,
    CGPathAddLineToPoint, CGPathAddArcToPoint, CGPathCloseSubpath,
)

W, H, RB = 320.0, 62.0, 22.0
NOTCH_TOP = 32.0
ACCENT = (0.12, 0.56, 1.0)
GREEN = (0.20, 0.84, 0.38)
STATE = {"i": 0, "total": 1, "text": "Starting…", "done": False}


def _cg(rgb, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], a).CGColor()


def _white(a=1.0):
    return NSColor.colorWithCalibratedWhite_alpha_(1.0, a).CGColor()


def _ease_out():
    try:
        return CAMediaTimingFunction.functionWithControlPoints____(0.23, 1.0, 0.32, 1.0)
    except Exception:
        return CAMediaTimingFunction.functionWithName_("easeOut")


def _reduce_motion():
    try:
        return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
    except Exception:
        return False


def _notch_center(screen):
    try:
        l = screen.auxiliaryTopLeftArea().size.width
        r = screen.auxiliaryTopRightArea().origin.x
        if r > l > 0:
            return (l + r) / 2.0
    except Exception:
        pass
    return screen.frame().size.width / 2.0


def _notch_path(w, h, rb):
    p = CGPathCreateMutable()
    CGPathMoveToPoint(p, None, 0, h)
    CGPathAddLineToPoint(p, None, w, h)
    CGPathAddArcToPoint(p, None, w, 0, 0, 0, rb)
    CGPathAddArcToPoint(p, None, 0, 0, 0, h, rb)
    CGPathAddLineToPoint(p, None, 0, h)
    CGPathCloseSubpath(p)
    return p


def _text_layer(x, y, w, h, size, bold, a=1.0):
    t = CATextLayer.layer()
    t.setContentsScale_(2.0)
    t.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    t.setFontSize_(size)
    t.setForegroundColor_(_white(a))
    t.setFrame_(CGRectMake(x, y, w, h))
    return t


class NotchIsland:
    def step(self, i, total, text):
        STATE.update(i=i, total=total, text=text)

    def finish(self, text="Done"):
        STATE.update(text=text, done=True, i=STATE["total"])
        self._done_at = time.time()

    def run(self, target):
        self._done_at = None
        self._popped = False
        self._reduce = _reduce_motion()
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        screen = NSScreen.mainScreen()
        sf = screen.frame()
        x = _notch_center(screen) - W / 2.0
        y = sf.size.height - H

        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, W, H), NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        win.setLevel_(NSStatusWindowLevel)
        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary)
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setIgnoresMouseEvents_(True)
        win.setHasShadow_(False)                          # no rectangular window shadow

        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        view.setWantsLayer_(True)
        lay = view.layer()
        win.setContentView_(view)

        shape = _notch_path(W, H, RB)
        # BLACK panel (matches the notch), masked to the notch shape: top is pure black to blend,
        # a hair lighter at the bottom for just enough depth.
        grad = CAGradientLayer.layer()
        grad.setFrame_(CGRectMake(0, 0, W, H))
        grad.setColors_([_cg((0.0, 0.0, 0.0), 1.0), _cg((0.07, 0.07, 0.085), 1.0)])
        grad.setStartPoint_((0.5, 1.0)); grad.setEndPoint_((0.5, 0.0))
        gmask = CAShapeLayer.layer(); gmask.setPath_(shape); grad.setMask_(gmask)
        lay.addSublayer_(grad)
        # soft shadow that follows the SHAPE (not the window box)
        grad.setShadowColor_(NSColor.blackColor().CGColor())
        grad.setShadowOpacity_(0.35); grad.setShadowRadius_(8.0); grad.setShadowOffset_((0, -2))
        grad.setShadowPath_(shape)

        cy = (H - NOTCH_TOP) / 2.0 + 1
        d = 16.0
        spin = CAShapeLayer.layer()
        spin.setBounds_(CGRectMake(0, 0, d, d)); spin.setPosition_((26.0, cy))
        spin.setPath_(CGPathCreateWithEllipseInRect(CGRectMake(0, 0, d, d), None))
        spin.setStrokeColor_(_cg(ACCENT)); spin.setFillColor_(NSColor.clearColor().CGColor())
        spin.setLineWidth_(2.3); spin.setLineCap_("round")
        spin.setStrokeStart_(0.0); spin.setStrokeEnd_(0.72)
        if not self._reduce:
            rot = CABasicAnimation.animationWithKeyPath_("transform.rotation.z")
            rot.setFromValue_(0.0); rot.setToValue_(-6.2831853); rot.setDuration_(0.7)
            rot.setRepeatCount_(1e9); spin.addAnimation_forKey_(rot, "spin")
        lay.addSublayer_(spin); self._spin = spin

        self._title = _text_layer(46, cy - 1, W - 150, 17, 12.5, True)
        self._sub = _text_layer(46, cy - 15, W - 150, 13, 9.5, False, a=0.55)
        lay.addSublayer_(self._title); lay.addSublayer_(self._sub)

        self._px, self._pw = W - 90, 68.0
        track = CAShapeLayer.layer()
        track.setFrame_(CGRectMake(self._px, cy - 1, self._pw, 4)); track.setCornerRadius_(2)
        track.setBackgroundColor_(_white(0.16)); lay.addSublayer_(track)
        self._fill = CAShapeLayer.layer()
        self._fill.setFrame_(CGRectMake(self._px, cy - 1, 3, 4)); self._fill.setCornerRadius_(2)
        self._fill.setBackgroundColor_(_cg(ACCENT)); lay.addSublayer_(self._fill)
        self._cy = cy; self._lay = lay

        win.orderFrontRegardless(); self._win = win
        self._enter(lay, [spin, self._title, self._sub, track, self._fill])
        self._sync()
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.1, True, lambda t: self._sync())

        def _wrap():
            try:
                target()
            finally:
                if not STATE["done"]:
                    self.finish()
        threading.Thread(target=_wrap, daemon=True).start()
        app.run()

    def _enter(self, lay, content):
        eo = _ease_out()
        if self._reduce:
            self._win.setAlphaValue_(0.0)
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(0.18)
            self._win.animator().setAlphaValue_(1.0)
            NSAnimationContext.endGrouping()
            return
        try:
            lay.setAnchorPoint_((0.5, 1.0)); lay.setPosition_((W / 2.0, H))
            sp = CASpringAnimation.animationWithKeyPath_("transform.scale")
            sp.setMass_(1.0); sp.setStiffness_(300.0); sp.setDamping_(24.0)
            sp.setFromValue_(0.9); sp.setToValue_(1.0); sp.setDuration_(sp.settlingDuration())
            lay.addAnimation_forKey_(sp, "in")
            op = CABasicAnimation.animationWithKeyPath_("opacity")
            op.setFromValue_(0.0); op.setToValue_(1.0); op.setDuration_(0.26); op.setTimingFunction_(eo)
            lay.addAnimation_forKey_(op, "fade")
        except Exception:
            pass
        t0 = CACurrentMediaTime()
        for idx, sub in enumerate(content):
            a = CABasicAnimation.animationWithKeyPath_("opacity")
            a.setFromValue_(0.0); a.setToValue_(1.0); a.setDuration_(0.22)
            a.setBeginTime_(t0 + 0.06 + idx * 0.045); a.setTimingFunction_(eo)
            a.setFillMode_("backwards")
            sub.addAnimation_forKey_(a, "stagger")

    def _pop(self):
        if self._reduce:
            return
        try:
            s = CASpringAnimation.animationWithKeyPath_("transform.scale")
            s.setMass_(1.0); s.setStiffness_(420.0); s.setDamping_(12.0)
            s.setFromValue_(1.04); s.setToValue_(1.0); s.setDuration_(s.settlingDuration())
            self._lay.addAnimation_forKey_(s, "pop")
        except Exception:
            pass

    def _sync(self):
        st = STATE
        CATransaction.begin(); CATransaction.setDisableActions_(True)
        msg = st["text"]
        self._title.setString_((msg[:30] + "…") if len(msg) > 31 else msg)
        self._sub.setString_("complete" if st["done"] else f"step {st['i']} of {st['total']}")
        frac = max(0.04, min(1.0, st["i"] / max(1, st["total"])))
        self._fill.setFrame_(CGRectMake(self._px, self._cy - 1, self._pw * frac, 4))
        if st["done"]:
            self._spin.setStrokeColor_(_cg(GREEN)); self._spin.setStrokeEnd_(1.0)
            self._spin.removeAnimationForKey_("spin"); self._fill.setBackgroundColor_(_cg(GREEN))
        CATransaction.commit()
        if st["done"] and not self._popped:
            self._popped = True; self._pop()
        if self._done_at and time.time() - self._done_at > 1.6:
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(0.2)
            self._win.animator().setAlphaValue_(0.0)
            NSAnimationContext.endGrouping()
            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                0.24, False, lambda t: NSApplication.sharedApplication().terminate_(None))
