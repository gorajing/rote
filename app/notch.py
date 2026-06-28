"""A native-feeling Dynamic-Island companion at the MacBook notch (AppKit / PyObjC).

This is the *persistent* voice companion: one surface that morphs through the whole interaction —
idle (a small pill) → listening → thinking → speaking → the task replay ring → back to the pill —
without ever disappearing. It is **pure UI**: it renders from a module-level STATE dict and knows
nothing about sockets or voice (app/notch_daemon.py feeds it).

Design lenses (kept from the original, extended for the companion):
  - emil-design-eng: pill⇄expanded is an *interruptible* spring (Apple spring, from scale 0.9 — never
    0); collapse is a snappy ease-out (asymmetric). State-text swaps crossfade. Listening pulses on
    the live mic level. Done draws a green check + spring pop. Honors prefers-reduced-motion.
  - impeccable: Restrained color — tinted near-black body, pure black only at the very top edge to
    blend the physical notch bezel, ONE accent (the user's controlAccentColor). Title = state verb
    (SF semibold); subtitle = content (regular, dim). "step x of y" only while working.
  - frontend-design: native is the bold move — SF Pro + system accent + hardware-blended black, so it
    reads as a real macOS system feature, not an app. No orb, no waveform, no purple.

Threading: AppKit owns the main thread + run loop; a worker thread mutates STATE; a 0.1s main-thread
timer reconciles STATE → layers. Same split the rest of the app relies on.
"""
import math
import random
import time
import threading

# AppKit/Quartz (PyObjC) is required to actually render the notch, but the STATE dict and the
# protocol-facing logic (used by the daemon + its tests) must import without it. Guard the import so
# `import app.notch` works on machines/CI without PyObjC; run() raises a clear error there.
try:
    from AppKit import (
        NSApplication, NSApplicationActivationPolicyAccessory, NSWindow, NSView, NSColor, NSFont,
        NSColorSpace, NSFontWeightSemibold, NSFontWeightRegular, NSScreen, NSWorkspace, NSTimer,
        NSMakeRect, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, NSStatusWindowLevel,
        NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorStationary,
        NSWindowCollectionBehaviorFullScreenAuxiliary, NSAnimationContext,
    )
    from Quartz import (
        CAShapeLayer, CAGradientLayer, CATextLayer, CABasicAnimation, CASpringAnimation, CATransaction,
        CAMediaTimingFunction, CACurrentMediaTime, CGRectMake, CGPathCreateMutable, CGPathAddArc,
        CGPathMoveToPoint, CGPathAddLineToPoint,
    )
    _HAS_APPKIT = True
except Exception:                                    # pragma: no cover - only on non-PyObjC envs
    _HAS_APPKIT = False

W, H, RB = 322.0, 86.0, 28.0           # expanded panel
PILL_W, PILL_H, PILL_R = 124.0, 34.0, 16.0
NOTCH_TOP = 32.0
RING = 26.0
GREEN = (0.20, 0.84, 0.38)
DANGER = (0.94, 0.36, 0.34)
BOTTOM_CORNERS = 3                      # kCALayerMinXMinYCorner | kCALayerMaxXMinYCorner (y-up layer)

# The single source of truth the worker thread writes and the timer renders.
STATE = {"mode": "idle", "title": "Rote", "subtitle": "", "i": 0, "total": 1, "level": 0.0, "seq": 0}

EXPANDED_MODES = {"listening", "thinking", "speaking", "working", "done", "error"}


def _cg(rgb, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], a).CGColor()


def _white(a=1.0):
    return NSColor.colorWithCalibratedWhite_alpha_(1.0, a).CGColor()


def _accent_cg():
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


def _ring_path(box, r):
    """Circle from 12 o'clock, clockwise — so strokeEnd reads as progress."""
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
    # ---- public API (thread-safe writes) ----
    def update(self, **kw):
        STATE.update(kw)
        STATE["seq"] += 1

    def step(self, i, total, text):                 # legacy replay API -> working mode
        self.update(mode="working", i=i, total=total, title=text,
                    subtitle=f"step {i} of {total}")

    def finish(self, text="Done"):
        self.update(mode="done", title=text, i=STATE.get("total", 1))
        self._done_at = time.time()

    def status(self, text):                         # legacy: update the line, stay expanded
        mode = STATE.get("mode")
        self.update(mode=mode if mode in EXPANDED_MODES else "working", title=text)

    def serve(self, target):
        """Persistent companion: never auto-terminates; 'done' collapses back to the pill."""
        self.run(target, persistent=True)

    # ---- run loop ----
    def run(self, target, persistent=False):
        if not _HAS_APPKIT:
            raise RuntimeError("notch HUD requires PyObjC (AppKit/Quartz), which isn't installed here")
        self._persistent = persistent
        self._done_at = None
        self._reduce = _reduce_motion()
        self._accent = _accent_cg()
        self._rendered_mode = None
        self._frac = 0.0
        self._cur_w = None

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
        self._win = win; self._lay = lay

        # --- panel: rounded-bottom, flush-top, grows DOWN from the notch (anchor top-center) ---
        panel = CAGradientLayer.layer()
        panel.setColors_([_cg((0.0, 0.0, 0.0), 1.0), _cg((0.05, 0.05, 0.07), 1.0)])
        panel.setStartPoint_((0.5, 1.0)); panel.setEndPoint_((0.5, 0.0))
        panel.setCornerRadius_(PILL_R)
        try:
            panel.setMaskedCorners_(BOTTOM_CORNERS)
        except Exception:
            pass
        panel.setAnchorPoint_((0.5, 1.0)); panel.setPosition_((W / 2.0, H))
        panel.setBounds_(CGRectMake(0, 0, PILL_W, PILL_H))
        panel.setShadowColor_(NSColor.blackColor().CGColor()); panel.setShadowOpacity_(0.32)
        panel.setShadowRadius_(9.0); panel.setShadowOffset_((0, -2))
        lay.addSublayer_(panel); self._panel = panel

        cy = (H - NOTCH_TOP) / 2.0 + 2
        self._cy = cy
        self._pos_pill = (W / 2.0, H - PILL_H / 2.0)
        self._pos_expanded = (32.0, cy)

        # --- left indicator: a dot (pill/listening/speaking), a determinate ring (working),
        #     a spinner arc (thinking), and a check (done). Same container, crossfaded by mode. ---
        ind = CAShapeLayer.layer()
        ind.setBounds_(CGRectMake(0, 0, RING, RING)); ind.setPosition_(self._pos_pill)
        rp = _ring_path(RING, (RING - 3.0) / 2.0)

        track = CAShapeLayer.layer(); track.setBounds_(ind.bounds()); track.setPosition_((RING / 2, RING / 2))
        track.setPath_(rp); track.setFillColor_(NSColor.clearColor().CGColor())
        track.setStrokeColor_(_white(0.14)); track.setLineWidth_(3.0); track.setOpacity_(0.0)
        ind.addSublayer_(track); self._track = track

        fill = CAShapeLayer.layer(); fill.setBounds_(ind.bounds()); fill.setPosition_((RING / 2, RING / 2))
        fill.setPath_(rp); fill.setFillColor_(NSColor.clearColor().CGColor())
        fill.setStrokeColor_(self._accent); fill.setLineWidth_(3.0); fill.setLineCap_("round")
        fill.setStrokeStart_(0.0); fill.setStrokeEnd_(0.0)
        ind.addSublayer_(fill); self._fill = fill

        spin = CAShapeLayer.layer(); spin.setBounds_(ind.bounds()); spin.setPosition_((RING / 2, RING / 2))
        spin.setPath_(rp); spin.setFillColor_(NSColor.clearColor().CGColor())
        spin.setStrokeColor_(self._accent); spin.setLineWidth_(3.0); spin.setLineCap_("round")
        spin.setStrokeStart_(0.0); spin.setStrokeEnd_(0.28); spin.setOpacity_(0.0)
        ind.addSublayer_(spin); self._spin = spin

        check = CAShapeLayer.layer(); check.setBounds_(ind.bounds()); check.setPosition_((RING / 2, RING / 2))
        check.setPath_(_check_path(RING)); check.setFillColor_(NSColor.clearColor().CGColor())
        check.setStrokeColor_(_cg(GREEN)); check.setLineWidth_(2.6)
        check.setLineCap_("round"); check.setLineJoin_("round"); check.setStrokeEnd_(0.0)
        ind.addSublayer_(check); self._check = check

        dot = CAShapeLayer.layer(); dot.setBounds_(CGRectMake(0, 0, RING, RING))
        dot.setPosition_((RING / 2, RING / 2))
        dpath = CGPathCreateMutable(); CGPathAddArc(dpath, None, RING / 2, RING / 2, 4.0, 0, 2 * math.pi, True)
        dot.setPath_(dpath); dot.setFillColor_(self._accent); dot.setStrokeColor_(NSColor.clearColor().CGColor())
        ind.addSublayer_(dot); self._dot = dot

        lay.addSublayer_(ind); self._ind = ind

        # --- text (hidden in the pill) ---
        self._title = _text_layer(58, cy + 1, W - 76, 20,
                                  NSFont.systemFontOfSize_weight_(13.5, NSFontWeightSemibold))
        self._sub = _text_layer(58, cy - 17, W - 76, 15,
                                NSFont.systemFontOfSize_weight_(10.5, NSFontWeightRegular), a=0.5)
        self._title.setOpacity_(0.0); self._sub.setOpacity_(0.0)
        lay.addSublayer_(self._title); lay.addSublayer_(self._sub)

        win.orderFrontRegardless()
        self._enter()
        self._sync()
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.08, True, lambda t: self._sync())

        def _wrap():
            try:
                target()
            finally:
                if not self._persistent and STATE["mode"] not in ("done", "error"):
                    self.finish()
        threading.Thread(target=_wrap, daemon=True).start()
        app.run()

    # ---- entrance ----
    def _enter(self):
        if self._reduce:
            self._win.setAlphaValue_(0.0)
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(0.18)
            self._win.animator().setAlphaValue_(1.0)
            NSAnimationContext.endGrouping()
            return
        try:
            self._lay.setAnchorPoint_((0.5, 1.0)); self._lay.setPosition_((W / 2.0, H))
            sp = CASpringAnimation.animationWithKeyPath_("transform.scale")
            sp.setMass_(1.0); sp.setStiffness_(300.0); sp.setDamping_(24.0)
            sp.setFromValue_(0.9); sp.setToValue_(1.0); sp.setDuration_(sp.settlingDuration())
            self._lay.addAnimation_forKey_(sp, "in")
            op = CABasicAnimation.animationWithKeyPath_("opacity")
            op.setFromValue_(0.0); op.setToValue_(1.0); op.setDuration_(0.26); op.setTimingFunction_(_ease_out())
            self._lay.addAnimation_forKey_(op, "fade")
        except Exception:
            pass

    # ---- per-mode helpers ----
    def _resize(self, w, h, radius):
        """Morph the panel between pill and expanded. Uses a reliable implicit animation with a
        strong ease-out (springs on bounds.size are unreliable through PyObjC); the entrance and the
        done-pop still use transform.scale springs, which are proven elsewhere in this file."""
        if self._cur_w == w:
            return
        self._cur_w = w
        CATransaction.begin()
        if self._reduce:
            CATransaction.setDisableActions_(True)
        else:
            CATransaction.setAnimationDuration_(0.40)
            CATransaction.setAnimationTimingFunction_(_ease_out())
        self._panel.setCornerRadius_(radius)
        self._panel.setBounds_(CGRectMake(0, 0, w, h))
        CATransaction.commit()

    def _move_ind(self, pos):
        CATransaction.begin(); CATransaction.setDisableActions_(self._reduce)
        if not self._reduce:
            CATransaction.setAnimationDuration_(0.32)
            CATransaction.setAnimationTimingFunction_(_ease_out())
        self._ind.setPosition_(pos)
        CATransaction.commit()

    def _show(self, layer, on, dur=0.18):
        CATransaction.begin(); CATransaction.setAnimationDuration_(0.0 if self._reduce else dur)
        layer.setOpacity_(1.0 if on else 0.0)
        CATransaction.commit()

    def _pulse_dot(self, level):
        scale = 1.0 + max(0.0, min(1.0, level)) * 0.55
        CATransaction.begin(); CATransaction.setAnimationDuration_(0.0 if self._reduce else 0.12)
        self._dot.setTransform_(_scale_transform(scale))
        CATransaction.commit()

    def _speaking_level(self):
        """A natural 'talking' envelope for the dot while Rote speaks (two oscillators + light
        jitter). Bounded to the speaking state, so it reads as a voice without tapping TTS audio."""
        t = time.time() - getattr(self, "_spk_t0", 0.0)
        v = 0.30 + 0.32 * abs(math.sin(t * 7.5)) + 0.18 * abs(math.sin(t * 3.1 + 1.0))
        return max(0.12, min(1.0, v + random.uniform(-0.07, 0.07)))

    def _set_ring(self, frac):
        if abs(frac - self._frac) < 0.001:
            return
        if not self._reduce:
            a = CABasicAnimation.animationWithKeyPath_("strokeEnd")
            a.setFromValue_(self._frac); a.setToValue_(frac); a.setDuration_(0.4); a.setTimingFunction_(_ease_out())
            self._fill.addAnimation_forKey_(a, "grow")
        self._fill.setStrokeEnd_(frac); self._frac = frac

    def _spin_on(self, on):
        if on and not self._reduce:
            r = CABasicAnimation.animationWithKeyPath_("transform.rotation.z")
            r.setFromValue_(0.0); r.setToValue_(-2 * math.pi); r.setDuration_(0.9); r.setRepeatCount_(1e9)
            self._spin.addAnimation_forKey_(r, "spin")
        else:
            self._spin.removeAnimationForKey_("spin")

    def _draw_check(self):
        self._fill.setStrokeColor_(_cg(GREEN)); self._set_ring(1.0)
        if not self._reduce:
            a = CABasicAnimation.animationWithKeyPath_("strokeEnd")
            a.setFromValue_(0.0); a.setToValue_(1.0); a.setDuration_(0.28); a.setTimingFunction_(_ease_out())
            self._check.addAnimation_forKey_(a, "draw")
            s = CASpringAnimation.animationWithKeyPath_("transform.scale")
            s.setMass_(1.0); s.setStiffness_(420.0); s.setDamping_(12.0)
            s.setFromValue_(1.06); s.setToValue_(1.0); s.setDuration_(s.settlingDuration())
            self._panel.addAnimation_forKey_(s, "pop")
        self._check.setStrokeEnd_(1.0)

    # ---- mode transitions (run once when STATE['mode'] changes) ----
    def _apply_mode(self, mode):
        expanded = mode in EXPANDED_MODES
        self._resize(W if expanded else PILL_W, H if expanded else PILL_H, RB if expanded else PILL_R)
        self._move_ind(self._pos_expanded if expanded else self._pos_pill)
        self._show(self._title, expanded and mode not in ("",))
        self._show(self._sub, expanded)

        dot_on = mode in ("idle", "listening", "speaking")
        self._show(self._dot, dot_on)
        self._show(self._track, mode == "working")
        self._show(self._fill, mode in ("working", "done"))
        self._show(self._spin, mode == "thinking")
        self._show(self._check, mode == "done")
        self._spin_on(mode == "thinking")

        if mode != "working":
            self._frac = 0.0; self._fill.setStrokeEnd_(0.0)
        if mode == "working":
            self._fill.setStrokeColor_(self._accent)
        if mode in ("idle", "listening", "speaking"):
            self._dot.setFillColor_(_cg(DANGER) if mode == "error" else self._accent)
        if mode == "error":
            self._show(self._dot, True); self._dot.setFillColor_(_cg(DANGER))
        if mode == "done":
            self._draw_check()
        if mode == "speaking":
            self._spk_t0 = time.time()
        if mode not in ("listening", "speaking"):
            self._dot.setTransform_(_scale_transform(1.0))   # clear any leftover pulse scale
        if mode == "idle" and not self._reduce:
            br = CABasicAnimation.animationWithKeyPath_("transform.scale")
            br.setFromValue_(1.0); br.setToValue_(1.12); br.setDuration_(1.4)
            br.setAutoreverses_(True); br.setRepeatCount_(1e9)
            self._dot.addAnimation_forKey_(br, "breathe")
        else:
            self._dot.removeAnimationForKey_("breathe")

    # ---- 0.08s reconcile ----
    def _sync(self):
        st = STATE
        mode = st["mode"]
        if mode != self._rendered_mode:
            self._apply_mode(mode)
            self._rendered_mode = mode

        if mode in ("listening", "speaking", "working", "thinking", "done", "error"):
            CATransaction.begin(); CATransaction.setDisableActions_(True)
            msg = st["title"] or ""
            self._title.setString_((msg[:34] + "…") if len(msg) > 35 else msg)
            self._sub.setString_(st["subtitle"] or "")
            CATransaction.commit()

        if mode == "working":
            frac = max(0.0, min(1.0, st["i"] / max(1, st["total"])))
            self._set_ring(frac)
        if mode == "speaking":
            self._pulse_dot(self._speaking_level())      # alive while Rote talks
        elif mode == "listening":
            self._pulse_dot(st.get("level", 0.0))         # driven by your mic VAD

        # persistent companion: a finished task settles, then collapses back to the pill
        if mode == "done" and self._done_at and time.time() - self._done_at > 1.6:
            if self._persistent:
                self.update(mode="idle", title="Rote", subtitle="")
                self._done_at = None
            else:
                self._teardown()

    def _teardown(self):
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.2)
        self._win.animator().setAlphaValue_(0.0)
        NSAnimationContext.endGrouping()
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.24, False, lambda t: NSApplication.sharedApplication().terminate_(None))


def _scale_transform(s):
    from Quartz import CATransform3DMakeScale
    return CATransform3DMakeScale(s, s, 1.0)
