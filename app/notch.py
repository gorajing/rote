"""A premium Dynamic-Island-style overlay that MERGES with the MacBook notch (AppKit / PyObjC).

vs. a floating pill, this panel sits flush against the screen top and is masked to a custom
shape — square top corners that blend into the black notch, rounded bottom — so it reads as the
notch *growing downward*. Built with the techniques the native notch apps use:
  - NSVisualEffectView dark material, masked to the notch shape -> frosted glass in the right shape
  - Core Animation layers (CAShapeLayer / CATextLayer) -> GPU-smooth, crisp at retina
  - a CABasicAnimation rotating-arc spinner -> buttery, no timer redraw
  - hairline border along the shape + soft shadow + grow-in fade

The replay runs on a worker thread and pushes status into STATE; a light main-thread timer syncs
the text/progress layers. Joins all Spaces, sits above the menu bar, ignores mouse events.
"""
import time
import threading

from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory, NSWindow, NSColor, NSFont, NSScreen,
    NSTimer, NSMakeRect, NSVisualEffectView, NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectStateActive, NSAppearance, NSWindowStyleMaskBorderless, NSBackingStoreBuffered,
    NSStatusWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary, NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSAnimationContext,
)
from Quartz import (
    CAShapeLayer, CATextLayer, CABasicAnimation, CATransaction, CGRectMake,
    CGPathCreateWithEllipseInRect, CGPathCreateMutable, CGPathMoveToPoint,
    CGPathAddLineToPoint, CGPathAddArcToPoint, CGPathCloseSubpath,
)

W, H, RB = 330.0, 64.0, 24.0      # width, height, bottom-corner radius
NOTCH_TOP = 32.0                  # the notch/menubar strip; content must sit BELOW it
ACCENT = (0.04, 0.52, 1.0)
GREEN = (0.18, 0.82, 0.36)
STATE = {"i": 0, "total": 1, "text": "Starting…", "done": False}


def _cg(rgb, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], a).CGColor()


def _white(a=1.0):
    return NSColor.colorWithCalibratedWhite_alpha_(1.0, a).CGColor()


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
    """Flush-top (square corners blend into the notch), rounded-bottom shape."""
    p = CGPathCreateMutable()
    CGPathMoveToPoint(p, None, 0, h)
    CGPathAddLineToPoint(p, None, w, h)                 # top edge (flush with screen top)
    CGPathAddArcToPoint(p, None, w, 0, 0, 0, rb)        # round bottom-right
    CGPathAddArcToPoint(p, None, 0, 0, 0, h, rb)        # round bottom-left
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
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        screen = NSScreen.mainScreen()
        sf = screen.frame()
        x = _notch_center(screen) - W / 2.0
        y = sf.size.height - H                          # FLUSH with the screen top

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
        win.setHasShadow_(True)

        fx = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        try:
            fx.setMaterial_(18)                          # NSVisualEffectMaterialHUDWindow
        except Exception:
            pass
        fx.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        fx.setState_(NSVisualEffectStateActive)
        fx.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameVibrantDark"))
        fx.setWantsLayer_(True)
        lay = fx.layer()

        shape = _notch_path(W, H, RB)
        mask = CAShapeLayer.layer(); mask.setPath_(shape)
        lay.setMask_(mask)                               # frosted glass clipped to the notch shape
        border = CAShapeLayer.layer()
        border.setPath_(shape); border.setFillColor_(NSColor.clearColor().CGColor())
        border.setStrokeColor_(_white(0.16)); border.setLineWidth_(1.2)
        lay.addSublayer_(border)
        win.setContentView_(fx)

        cy = (H - NOTCH_TOP) / 2.0 + 1                   # vertical center of the BELOW-notch area
        # spinner
        d = 16.0
        spin = CAShapeLayer.layer()
        spin.setBounds_(CGRectMake(0, 0, d, d)); spin.setPosition_((26.0, cy))
        spin.setPath_(CGPathCreateWithEllipseInRect(CGRectMake(0, 0, d, d), None))
        spin.setStrokeColor_(_cg(ACCENT)); spin.setFillColor_(NSColor.clearColor().CGColor())
        spin.setLineWidth_(2.3); spin.setLineCap_("round")
        spin.setStrokeStart_(0.0); spin.setStrokeEnd_(0.72)
        rot = CABasicAnimation.animationWithKeyPath_("transform.rotation.z")
        rot.setFromValue_(0.0); rot.setToValue_(-6.2831853); rot.setDuration_(0.85)
        rot.setRepeatCount_(1e9)
        spin.addAnimation_forKey_(rot, "spin")
        lay.addSublayer_(spin); self._spin = spin

        self._title = _text_layer(46, cy - 1, W - 150, 17, 12.5, True)
        self._sub = _text_layer(46, cy - 15, W - 150, 13, 9.5, False, a=0.55)
        lay.addSublayer_(self._title); lay.addSublayer_(self._sub)

        self._px, self._pw = W - 92, 70.0
        track = CAShapeLayer.layer()
        track.setFrame_(CGRectMake(self._px, cy - 1, self._pw, 4)); track.setCornerRadius_(2)
        track.setBackgroundColor_(_white(0.18)); lay.addSublayer_(track)
        self._fill = CAShapeLayer.layer()
        self._fill.setFrame_(CGRectMake(self._px, cy - 1, 3, 4)); self._fill.setCornerRadius_(2)
        self._fill.setBackgroundColor_(_cg(ACCENT)); lay.addSublayer_(self._fill)
        self._cy = cy

        win.setAlphaValue_(0.0); win.orderFrontRegardless(); self._win = win
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.28)
        win.animator().setAlphaValue_(1.0)
        NSAnimationContext.endGrouping()
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
        if self._done_at and time.time() - self._done_at > 1.7:
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(0.3)
            self._win.animator().setAlphaValue_(0.0)
            NSAnimationContext.endGrouping()
            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                0.35, False, lambda t: NSApplication.sharedApplication().terminate_(None))
