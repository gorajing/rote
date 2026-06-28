"""Self-test for the fusion dispatcher (app.fusion.dispatch).

PROVES the tiered-replay engine works WITHOUT a real browser, desktop, or network, by driving
dispatch.replay through STUB implementations of the contract.Executor / contract.Verifier
protocols. Every tier the contract promises is exercised against the REAL dispatch code:

    keyboard step (no crop)        -> fires blind, tier="keyboard", 0 CU      (Shah's floor)
    spatial step, crop FOUND       -> re-grounded click, tier="crop", 0 CU    (our rung)
    spatial step, crop NOT FOUND   -> escalate ONE step, tier="model", +1 CU  (self-heal)
    Verifier(False) -> needs_recompile True ; Verifier(True) -> needs_recompile False

HIT/MISS are produced by the REAL cv2 template match inside dispatch._localize (the stub controls
only the screenshot bytes and the skill carries the crops) — so "the stub finds it" / "does not
find it" is the genuine perception mechanism, not a faked score.

The model-escalation path is made network-free by stubbing the LOWEST available seam:
cu_runner._client_lazy (the genai client factory that dispatch._escalate lazily imports). With it
stubbed, dispatch's real _escalate + _apply_cu_action run unchanged and route the model's single
function_call down onto executor.click_at — proving the escalation actually reaches the executor.

Run:
    PYTHONPATH=/Users/jinchoi/Code/rote /Users/jinchoi/Code/rote/.venv/bin/python app/fusion/_selftest.py
"""
from __future__ import annotations

import base64

import cv2
import numpy as np

from app.fusion import dispatch
from app.fusion.contract import (Executor, FusedSkill, Precondition, Step,
                                 Verifier)

THRESHOLD = dispatch.MATCH_THRESHOLD  # 0.72


# ── deterministic perception fixtures (real PNGs, real cv2 match) ────────────────────────────
def _png_bytes(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def _png_b64(img: np.ndarray) -> str:
    return base64.b64encode(_png_bytes(img)).decode()


def _make_fixtures():
    """A structured screenshot + a crop that IS present (HIT) and one that is NOT (MISS)."""
    h, w = 120, 160
    yy, xx = np.mgrid[0:h, 0:w]
    base = ((xx + yy) / (w + h) * 200).astype(np.uint8)
    shot = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)
    shot[20:50, 30:60] = (255, 0, 0)      # distinctive marker the HIT crop copies
    shot[25:45, 35:55] = (0, 255, 255)

    hit_crop = shot[18:52, 28:62].copy()  # exact sub-patch -> match score == 1.0
    miss_crop = np.zeros((34, 34, 3), np.uint8)  # pattern absent from the screenshot
    miss_crop[:, ::2] = (0, 0, 255)
    cv2.circle(miss_crop, (17, 17), 10, (0, 255, 0), -1)
    return _png_bytes(shot), _png_b64(hit_crop), _png_b64(miss_crop)


# ── stub Executor (satisfies contract.Executor structurally) ─────────────────────────────────
class StubExecutor:
    """Records every protocol call; never touches a browser/desktop. screenshot() returns a fixed
    PNG so dispatch._localize runs the real cv2 match deterministically."""

    surface = "browser"

    def __init__(self, shot_bytes: bytes):
        self._shot = shot_bytes
        self.calls: list[tuple] = []

    def screenshot(self) -> bytes:
        self.calls.append(("screenshot",))
        return self._shot

    def fire_keyboard(self, op: str, args: dict | None = None) -> dict:
        self.calls.append(("fire_keyboard", op, dict(args or {})))
        return {"ok": True, "op": op}

    def click_at(self, x: int, y: int, action: str = "click", args: dict | None = None) -> dict:
        self.calls.append(("click_at", int(x), int(y), action))
        return {"ok": True, "x": int(x), "y": int(y), "action": action}

    def settle(self, timeout: float = 3.0) -> None:
        self.calls.append(("settle", timeout))
        return None

    # convenience views over the recorded call log
    def of(self, kind: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == kind]


# ── stub Verifier (flip True/False) ──────────────────────────────────────────────────────────
class StubVerifier:
    def __init__(self, result: bool):
        self.result = result
        self.checked: list[FusedSkill] = []

    def check(self, skill: FusedSkill) -> bool:
        self.checked.append(skill)
        return self.result


# ── network-free model-escalation stub (lowest seam: cu_runner._client_lazy) ─────────────────
class _FakeFunctionCall:
    type = "function_call"

    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = arguments


class _FakeInteraction:
    def __init__(self, steps):
        self.steps = steps


class _FakeInteractions:
    def __init__(self, action):
        self._action = action
        self.created = 0

    def create(self, **kwargs):
        self.created += 1
        return _FakeInteraction([self._action])


class _FakeClient:
    def __init__(self, action):
        self.interactions = _FakeInteractions(action)


def _install_fake_cu(name: str, arguments: dict):
    """Stub the genai client factory dispatch._escalate lazily imports, so escalation runs the
    REAL dispatch routing with NO network. Returns the fake client for assertions + a restore fn."""
    import app.cu_runner as cu
    fake = _FakeClient(_FakeFunctionCall(name, arguments))
    original = cu._client_lazy
    cu._client_lazy = lambda: fake
    return fake, (lambda: setattr(cu, "_client_lazy", original))


# ── the skill under test ─────────────────────────────────────────────────────────────────────
def _build_skill(hit_b64: str, miss_b64: str) -> FusedSkill:
    return FusedSkill(
        name="selftest",
        surface="browser",
        target="about:blank",
        steps=[
            # (a) keyboard: no crop -> fire blind, 0 CU
            Step("type hello", "type", {"text": "hello"}),
            # (b) spatial HIT: crop present -> re-grounded click, 0 CU
            Step("click the marker", "click", {}, pre=Precondition(crop_b64=hit_b64)),
            # (c) spatial MISS: crop absent -> escalate one step, +1 CU
            Step("click the drifted target", "click", {}, pre=Precondition(crop_b64=miss_b64)),
        ],
    )


def main() -> int:
    shot, hit_b64, miss_b64 = _make_fixtures()

    # Protocol conformance of the stubs themselves (runtime_checkable structural match).
    assert isinstance(StubExecutor(shot), Executor), "StubExecutor is not a contract.Executor"
    assert isinstance(StubVerifier(True), Verifier), "StubVerifier is not a contract.Verifier"

    skill = _build_skill(hit_b64, miss_b64)

    # The model, when escalated, returns ONE function_call: a click at (500,500). Distinct from the
    # HIT re-grounded coords, so we can prove the MISS step's click came from escalation.
    fake, restore = _install_fake_cu("click", {"x": 500, "y": 500})
    try:
        ex = StubExecutor(shot)
        result = dispatch.replay(skill, ex, StubVerifier(False), threshold=THRESHOLD)
    finally:
        restore()

    steps = result["steps"]
    assert len(steps) == 3, f"expected 3 step results, got {len(steps)}"
    kb, hit, miss = steps

    # (a) keyboard fired blind, 0 CU
    assert kb.tier == "keyboard", f"keyboard step tier={kb.tier!r}"
    assert kb.cu_calls == 0, f"keyboard step spent CU: {kb.cu_calls}"
    assert kb.ok is True, "keyboard step not ok"
    assert ("fire_keyboard", "type", {"text": "hello"}) in ex.calls, "fire_keyboard not called"

    # (b) crop HIT clicked with 0 CU, re-grounded near the marker (NOT the escalation coords)
    assert hit.tier == "crop", f"HIT step tier={hit.tier!r} (expected crop)"
    assert hit.cu_calls == 0, f"HIT step spent CU: {hit.cu_calls}"
    assert hit.ok is True, "HIT step not ok"
    assert hit.score is not None and hit.score >= THRESHOLD, f"HIT score={hit.score}"
    clicks = ex.of("click_at")
    assert clicks, "no click_at recorded"
    hit_click = clicks[0]
    assert hit_click[1:3] != (500, 500), "HIT click used escalation coords (should be re-grounded)"

    # (c) crop MISS escalated: +1 CU, tier model, and the escalation actually reached the executor
    assert miss.tier == "model", f"MISS step tier={miss.tier!r} (expected model)"
    assert miss.cu_calls == 1, f"MISS step cu_calls={miss.cu_calls} (expected 1)"
    assert miss.score is not None and miss.score < THRESHOLD, f"MISS score={miss.score}"
    assert miss.ok is True, "escalated step should be ok (fake CU returned an action)"
    assert fake.interactions.created == 1, "escalation did not call the (fake) model exactly once"
    esc_click = clicks[-1]
    assert esc_click[1:3] == (500, 500), f"escalation did not route to click_at(500,500): {esc_click}"

    # CU accounting: total == sum of per-step, and only the MISS step paid
    assert result["cu_calls"] == 1, f"total cu_calls={result['cu_calls']} (expected 1)"
    assert sum(s.cu_calls for s in steps) == result["cu_calls"], "cu_calls accounting mismatch"

    # Verifier gate, fail-CLOSED: False -> needs_recompile True
    assert result["verified"] is False, "verifier False but verified True"
    assert result["needs_recompile"] is True, "verifier False but needs_recompile False"

    # Verifier gate, success: True -> needs_recompile False (re-run, same deterministic skill)
    fake2, restore2 = _install_fake_cu("click", {"x": 500, "y": 500})
    try:
        ex2 = StubExecutor(shot)
        result_ok = dispatch.replay(skill, ex2, StubVerifier(True), threshold=THRESHOLD)
    finally:
        restore2()
    assert result_ok["verified"] is True, "verifier True but verified False"
    assert result_ok["needs_recompile"] is False, "verifier True but needs_recompile True"
    assert result_ok["cu_calls"] == 1, "second run CU accounting changed"

    # Verifier that RAISES must fail CLOSED (not be read as success)
    class _Boom:
        def check(self, skill):
            raise RuntimeError("ground truth unavailable")
    fake3, restore3 = _install_fake_cu("click", {"x": 500, "y": 500})
    try:
        boom = dispatch.replay(skill, StubExecutor(shot), _Boom(), threshold=THRESHOLD)
    finally:
        restore3()
    assert boom["verified"] is False, "raising verifier was read as success (must fail closed)"
    assert boom["needs_recompile"] is True, "raising verifier did not flag recompile"

    print("PASS keyboard: tier=keyboard cu=0  fire_keyboard('type') fired blind")
    print(f"PASS crop HIT: tier=crop cu=0 score={hit.score:.3f}  re-grounded click={hit_click[1:3]}")
    print(f"PASS crop MISS: tier=model cu=1 score={miss.score:.3f}  escalation->click_at(500,500)")
    print(f"PASS cu accounting: total={result['cu_calls']} == sum(per-step)")
    print("PASS verifier False -> needs_recompile=True ; True -> False ; raises -> closed(False)")
    print("SELFTEST_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
