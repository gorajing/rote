"""A native-feeling Dynamic-Island status overlay at the MacBook notch (AppKit / PyObjC).

Designed against three lenses:
  - emil-design-eng: spring entrance from the notch, strong ease-out, smooth determinate motion,
    completion feedback, honors prefers-reduced-motion.
  - impeccable: killed the redundant linear progress bar (the ring already shows progress);
    tinted near-black (not pure #000) like real macOS dark surfaces; hierarchy via weight.
  - frontend-design: for a *native* component, native is the bold move — SF Pro, the user's own
    system accent color (controlAccentColor), and an Apple-style determinate progress ring.

The ring fills as steps complete, breathes while active, and resolves to a green check. Black top
edge blends into the notch; flush top, rounded bottom. Joins all Spaces, above the menu bar,
ignores mouse events (never steals focus). Replay runs on a worker thread; a main-thread timer syncs.
"""
import math
import time
import threading

from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory, NSWindow, NSView, NSColor, NSFont,
    NSColorSpace, NSFontWeightSemibold, NSFontWeightRegular, NSScreen, NSWorkspace, NSTimer, NSMakeRect,
    NSWindowStyleMaskBorderless, NSBackingStoreBuffered, NSStatusWindowLevel,
    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorStationary,
    NSWindowCollectionBehaviorFullScreenAuxiliary, NSAnimationContext,
)
from Quartz import (
    CAShapeLayer, CAGradientLayer, CATextLayer, CABasicAnimation, CASpringAnimation, CATransaction,
    CAMediaTimingFunction, CACurrentMediaTime, CGRectMake, CGPathCreateMutable, CGPathMoveToPoint,
    CGPathAddLineToPoint, CGPathAddArcToPoint, CGPathAddArc, CGPathCloseSubpath,
)

W, H, RB = 322.0, 86.0, 28.0
NOTCH_TOP = 32.0
RING = 26.0            # ring container box
GREEN = (0.20, 0.84, 0.38)
STATE = {"i": 0, "total": 1, "text": "Starting…", "done": False}


def _cg(rgb, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], a).CGColor()


def _white(a=1.0):
    return NSColor.colorWithCalibratedWhite_alpha_(1.0, a).CGColor()


def _accent_cg():
    """The user's own macOS accent color (most native), kept in RGB so it stays saturated."""
    try:
        c = NSColor.controlAccentColor().colorUsingColorSpace_(NSColorSpace.sRGBColorSpace())
        return c.CGColor()
    except Exception:
        try:
            return NSColor.controlAccentColor().CGColor()
        except Exception:
            return _cg((0.12, 0.56, 1.0))


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


def _panel_path(w, h, rb):
    p = CGPathCreateMutable()
    CGPathMoveToPoint(p, None, 0, h)
    CGPathAddLineToPoint(p, None, w, h)
    CGPathAddArcToPoint(p, None, w, 0, 0, 0, rb)
    CGPathAddArcToPoint(p, None, 0, 0, 0, h, rb)
    CGPathAddLineToPoint(p, None, 0, h)
    CGPathCloseSubpath(p)
    return p


def _ring_path(box, r):
    """Circle starting at 12 o'clock, clockwise — so strokeEnd reads as progress."""
    p = CGPathCreateMutable()
    CGPathAddArc(p, None, box / 2.0, box / 2.0, r, math.pi / 2.0, math.pi / 2.0 - 2 * math.pi, True)
    return p


def _check_path(box):
    p = CGPathCreateMutable()
    CGPathMoveToPoint(p, None, box * 0.30, box * 0.50)
    CGPathAddLineToPoint(p, None, box * 0.44, box * 0.36)
    CGPathAddLineToPoint(p, None, box * 0.70, box * 0.64)
    return p


def _text_layer(x, y, w, h, font, a=1.0):
    t = CATextLayer.layer()
    t.setContentsScale_(2.0)
    t.setFont_(font); t.setFontSize_(font.pointSize())
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
        self._frac = 0.0
        self._reduce = _reduce_motion()
        self._accent = _accent_cg()
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
        win.setOpaque_(False); win.setBackgroundColor_(NSColor.clearColor())
        win.setIgnoresMouseEvents_(True); win.setHasShadow_(False)

        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H)); view.setWantsLayer_(True)
        lay = view.layer(); win.setContentView_(view)

        shape = _panel_path(W, H, RB)
        grad = CAGradientLayer.layer(); grad.setFrame_(CGRectMake(0, 0, W, H))
        grad.setColors_([_cg((0.0, 0.0, 0.0), 1.0), _cg((0.05, 0.05, 0.07), 1.0)])  # cool-tinted near-black
        grad.setStartPoint_((0.5, 1.0)); grad.setEndPoint_((0.5, 0.0))
        gmask = CAShapeLayer.layer(); gmask.setPath_(shape); grad.setMask_(gmask)
        grad.setShadowColor_(NSColor.blackColor().CGColor()); grad.setShadowOpacity_(0.32)
        grad.setShadowRadius_(9.0); grad.setShadowOffset_((0, -2)); grad.setShadowPath_(shape)
        lay.addSublayer_(grad)

        cy = (H - NOTCH_TOP) / 2.0 + 2

        # --- determinate progress ring (Apple Activity-ring language) ---
        ring = CAShapeLayer.layer()
        ring.setBounds_(CGRectMake(0, 0, RING, RING)); ring.setPosition_((32.0, cy))
        rp = _ring_path(RING, (RING - 3.0) / 2.0)
        track = CAShapeLayer.layer(); track.setBounds_(ring.bounds()); track.setPosition_((RING / 2, RING / 2))
        track.setPath_(rp); track.setFillColor_(NSColor.clearColor().CGColor())
        track.setStrokeColor_(_white(0.14)); track.setLineWidth_(3.0)
        ring.addSublayer_(track)
        self._fillring = CAShapeLayer.layer()
        self._fillring.setBounds_(ring.bounds()); self._fillring.setPosition_((RING / 2, RING / 2))
        self._fillring.setPath_(rp); self._fillring.setFillColor_(NSColor.clearColor().CGColor())
        self._fillring.setStrokeColor_(self._accent); self._fillring.setLineWidth_(3.0)
        self._fillring.setLineCap_("round"); self._fillring.setStrokeStart_(0.0); self._fillring.setStrokeEnd_(0.0)
        ring.addSublayer_(self._fillring)
        self._check = CAShapeLayer.layer()
        self._check.setBounds_(ring.bounds()); self._check.setPosition_((RING / 2, RING / 2))
        self._check.setPath_(_check_path(RING)); self._check.setFillColor_(NSColor.clearColor().CGColor())
        self._check.setStrokeColor_(_cg(GREEN)); self._check.setLineWidth_(2.6)
        self._check.setLineCap_("round"); self._check.setLineJoin_("round"); self._check.setStrokeEnd_(0.0)
        ring.addSublayer_(self._check)
        lay.addSublayer_(ring); self._ring = ring
        if not self._reduce:                              # breathe while active
            br = CABasicAnimation.animationWithKeyPath_("transform.scale")
            br.setFromValue_(1.0); br.setToValue_(1.06); br.setDuration_(0.95)
            br.setAutoreverses_(True); br.setRepeatCount_(1e9)
            try:
                br.setTimingFunction_(CAMediaTimingFunction.functionWithName_("easeInEaseOut"))
            except Exception:
                pass
            ring.addAnimation_forKey_(br, "breathe")

        # --- text (SF Pro, weight hierarchy) ---
        self._title = _text_layer(58, cy + 1, W - 76, 20,
                                  NSFont.systemFontOfSize_weight_(13.5, NSFontWeightSemibold))
        self._sub = _text_layer(58, cy - 17, W - 76, 15,
                                NSFont.systemFontOfSize_weight_(10.5, NSFontWeightRegular), a=0.5)
        lay.addSublayer_(self._title); lay.addSublayer_(self._sub)
        self._cy = cy; self._lay = lay

        win.orderFrontRegardless(); self._win = win
        self._enter(lay, [ring, self._title, self._sub])
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
            a.setBeginTime_(t0 + 0.06 + idx * 0.05); a.setTimingFunction_(eo)
            a.setFillMode_("backwards"); sub.addAnimation_forKey_(a, "stagger")

    def _set_ring(self, frac):
        eo = _ease_out()
        a = CABasicAnimation.animationWithKeyPath_("strokeEnd")
        a.setFromValue_(self._frac); a.setToValue_(frac); a.setDuration_(0.45); a.setTimingFunction_(eo)
        self._fillring.addAnimation_forKey_(a, "grow")
        self._fillring.setStrokeEnd_(frac)
        self._frac = frac

    def _complete(self):
        self._popped = True
        if not self._reduce:
            self._ring.removeAnimationForKey_("breathe")
        self._fillring.setStrokeColor_(_cg(GREEN))
        self._set_ring(1.0)
        a = CABasicAnimation.animationWithKeyPath_("strokeEnd")
        a.setFromValue_(0.0); a.setToValue_(1.0); a.setDuration_(0.28); a.setTimingFunction_(_ease_out())
        self._check.setStrokeEnd_(1.0); self._check.addAnimation_forKey_(a, "draw")
        if not self._reduce:
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
        self._title.setString_((msg[:34] + "…") if len(msg) > 35 else msg)
        self._sub.setString_("complete" if st["done"] else f"step {st['i']} of {st['total']}")
        CATransaction.commit()
        frac = max(0.0, min(1.0, st["i"] / max(1, st["total"])))
        if not st["done"] and abs(frac - self._frac) > 0.001:
            self._set_ring(frac)
        if st["done"] and not self._popped:
            self._complete()
        if self._done_at and time.time() - self._done_at > 1.7:
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(0.2)
            self._win.animator().setAlphaValue_(0.0)
            NSAnimationContext.endGrouping()
            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                0.24, False, lambda t: NSApplication.sharedApplication().terminate_(None))
