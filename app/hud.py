"""A Dynamic-Island-style status pill for Rote desktop replays.

A dark, rounded, always-on-top overlay that hugs the top-center of the screen (by the notch).
It shows the current step, a live spinner, and a progress bar — and animates on the MAIN thread
while the automation runs on a WORKER thread, so even a 2s app-load reads as 'working', not frozen.

Tkinter is touched ONLY from the main thread; the worker just sets plain attributes via .step()."""
import time
import threading
import tkinter as tk

PILL_W, PILL_H = 460, 66
ACCENT = "#30D158"      # apple green
BG = "#0B0C0E"
SUB = "#8A8F98"
SPINNER = "◜◠◝◞◡◟"      # smooth rotating arc frames


def _round_rect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class Hud:
    def __init__(self, title="Rote"):
        self.title = title
        self._text = "Starting…"
        self._i, self._total = 0, 1
        self._done = False
        self._frame = 0
        self.root = tk.Tk()
        self.root.overrideredirect(True)            # no title bar
        self.root.wm_attributes("-topmost", True)
        try:                                        # true rounded transparency on macOS Aqua
            self.root.wm_attributes("-transparent", True)
            bg = "systemTransparent"
        except tk.TclError:
            bg = BG
        self.root.config(bg=bg)
        sw = self.root.winfo_screenwidth()
        x = (sw - PILL_W) // 2
        self.root.geometry(f"{PILL_W}x{PILL_H}+{x}+2")   # top-center, hugging the notch
        self.c = tk.Canvas(self.root, width=PILL_W, height=PILL_H, bg=bg, highlightthickness=0)
        self.c.pack()

    # ---- called from the WORKER thread (sets plain attrs only) ----
    def step(self, i, total, text):
        self._i, self._total, self._text = i, total, text

    def finish(self, text="Done ✓"):
        self._text, self._i, self._done = text, self._total, True

    # ---- main-thread render loop ----
    def _draw(self):
        c = self.c
        c.delete("all")
        _round_rect(c, 2, 2, PILL_W - 2, PILL_H - 2, 26, fill=BG, outline="#23262B")
        done = self._done
        spin = "✓" if done else SPINNER[self._frame % len(SPINNER)]
        col = ACCENT if done else "#0A84FF"
        c.create_oval(22, PILL_H // 2 - 11, 44, PILL_H // 2 + 11, fill=col, outline="")
        c.create_text(33, PILL_H // 2, text=spin, fill="white", font=("Helvetica Neue", 13, "bold"))
        head = f"{self.title}  ·  Step {self._i}/{self._total}" if not done else f"{self.title}  ·  complete"
        c.create_text(58, 20, text=head, anchor="w", fill=SUB, font=("Helvetica Neue", 11))
        msg = (self._text[:46] + "…") if len(self._text) > 47 else self._text
        c.create_text(58, 39, text=msg, anchor="w", fill="white", font=("Helvetica Neue", 13, "bold"))
        # progress bar
        x1, x2, y = 58, PILL_W - 26, PILL_H - 12
        _round_rect(c, x1, y, x2, y + 5, 2, fill="#23262B", outline="")
        frac = max(0.02, min(1.0, self._i / max(1, self._total)))
        _round_rect(c, x1, y, x1 + int((x2 - x1) * frac), y + 5, 2, fill=col, outline="")
        self._frame += 1

    def _tick(self):
        self._draw()
        if self._done_at and time.time() - self._done_at > 1.6:
            self.root.destroy()
            return
        if self._done and not self._done_at:
            self._done_at = time.time()
        self.root.after(70, self._tick)

    def run(self, target):
        """Start the worker `target()` in a thread and run the HUD on the main thread."""
        self._done_at = None
        threading.Thread(target=self._guarded(target), daemon=True).start()
        self.root.after(70, self._tick)
        self.root.mainloop()

    def _guarded(self, target):
        def wrap():
            try:
                target()
            finally:
                if not self._done:
                    self.finish()
        return wrap
