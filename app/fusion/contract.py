"""THE FUSED-SKILL CONTRACT — the single spec every fusion component builds to.

A Skill is a compiled program: a sequence of Steps, each lowered (by the compiler) to the
CHEAPEST primitive that reliably achieves its intent. Replay (the dispatcher) routes each
step by primitive:

    keyboard  (open_app/hotkey/key/type/wait)         -> fire blind, ZERO perception   (Shah's floor)
    spatial   (click/drag) + a visual Precondition     -> localize crop, ZERO model      (our rung)
    model                                              -> escalate ONE step to Gemini CU (self-heal)

Two Executors (browser=Playwright, desktop=pyautogui) implement the same protocol, so the
dispatcher, the lowering compiler, and the self-heal are all SURFACE-AGNOSTIC. Success is
decided by a Verifier reading GROUND TRUTH (browser: the arena /state checker; desktop: the
produced artifact) — never by the model's self-report.

Builders implement against the Protocols/dataclasses below and MUST NOT widen this surface
without updating this file first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Protocol, runtime_checkable

# ── primitive taxonomy ──────────────────────────────────────────────────────────────────
KEYBOARD_OPS = ("open_app", "hotkey", "key", "type", "wait")  # 0 perception, 0 model
SPATIAL_OPS = ("click", "drag")                               # cheap vision (crop), 0 model
MODEL_OP = "model"                                            # last resort: 1 Gemini CU call

Primitive = Literal["open_app", "hotkey", "key", "type", "wait", "click", "drag", "model"]
Surface = Literal["browser", "desktop"]

# Normalized coordinate space the MODEL emits and every Executor denormalizes to its surface.
COORD_MAX = 999


@dataclass
class Precondition:
    """A cheap, model-free check that a step's target is present before it fires.

    crop_b64 : PNG (base64) template to match on the current screenshot. For SPATIAL steps it
               both gates the step (present? ≥ threshold) and re-localizes the click target. For
               KEYBOARD steps it is an OPTIONAL confidence gate (confirm we're on the right screen
               before firing a blind shortcut — fixes open-loop blindness).
    settle   : if True, wait for the screen to stop changing before checking/acting.
    """
    crop_b64: Optional[str] = None
    settle: bool = True


@dataclass
class Step:
    """One lowered action. `primitive` decides which replay tier handles it; `args` carry the
    payload (keys/text/app for keyboard, normalized x/y for spatial). `intent` is the human
    description AND the prompt used if this step must escalate to the model."""
    intent: str
    primitive: Primitive
    args: dict = field(default_factory=dict)
    pre: Optional[Precondition] = None


@dataclass
class FusedSkill:
    """A compiled, replayable, verifiable skill. Produced by the lowering compiler from a
    checker-verified trajectory; consumed by the dispatcher."""
    name: str
    surface: Surface
    target: str                                   # desktop: app name; browser: url/site
    params: dict = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)
    verify: dict = field(default_factory=dict)    # Verifier spec, e.g.
    #   browser: {"kind": "checker", "checker": "dispute_workflow", "params": {...}}
    #   desktop: {"kind": "docx", "path": "~/Desktop/gemini.docx", "contains": "Hello..."}
    version: int = 1


@dataclass
class StepResult:
    """What the dispatcher records per step — drives the HUD and the recompile decision."""
    index: int
    primitive: Primitive
    tier: Literal["keyboard", "crop", "model"]
    cu_calls: int = 0            # 0 unless this step escalated
    score: Optional[float] = None  # crop-match score, when spatial
    ok: bool = True


@runtime_checkable
class Executor(Protocol):
    """A surface backend. BrowserExecutor (Playwright) and DesktopExecutor (pyautogui) both
    implement this; the dispatcher NEVER imports Playwright or pyautogui directly.

    All implementations must be FORGIVING: methods return a result dict and never raise on a
    failed action (return {"ok": False, "error": ...}); the dispatcher decides escalation."""
    surface: Surface

    def screenshot(self) -> bytes:
        """PNG bytes of the current screen at the SAME logical resolution the model sees, so
        normalized 0..COORD_MAX coords map consistently. Used for crop-match + model escalation."""
        ...

    def fire_keyboard(self, op: str, args: dict) -> dict:
        """Execute a KEYBOARD_OP (op in KEYBOARD_OPS) with no perception. e.g.
        hotkey {"keys": ["command", "s"]}, type {"text": "..."}, open_app {"name": "Microsoft Word"},
        key {"key": "enter"}, wait {"seconds": 1.0}. Returns {"ok": bool, ...}."""
        ...

    def click_at(self, x: int, y: int, action: str = "click", args: Optional[dict] = None) -> dict:
        """Execute a SPATIAL_OP at NORMALIZED 0..COORD_MAX coords (executor denormalizes to its
        surface). `action` in SPATIAL_OPS. Returns {"ok": bool, ...}."""
        ...

    def settle(self, timeout: float = 3.0) -> None:
        """Cheap, token-free wait until the screen stops changing (grayscale-diff change detect)."""
        ...


@runtime_checkable
class Verifier(Protocol):
    """Ground-truth success check — NEVER the model's self-report. Implementations read real
    state: the arena /state checker (browser) or the produced artifact (desktop)."""

    def check(self, skill: FusedSkill) -> bool:
        """Return True iff the skill's goal is actually achieved in ground truth. Must fail
        CLOSED (return False) on any error — a step that can't be verified is not a success."""
        ...
