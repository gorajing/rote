"""The durable self-heal (self-IMPROVEMENT) contract for the fusion dispatcher.

Proves the property that turns "self-heals" into "self-improves": when a spatial step's crop drifts,
the one-step CU escalation re-grounds it AND the crop is re-cut at the new location, so the NEXT
replay matches at 0 CU. Drift is paid ONCE, not forever. And the integrity guard: a heal that did
not re-VERIFY never touches the stored skill (no poisoning).

Hermetic: a synthetic screenshot + a mocked escalation — no live arena, no Gemini, no key.
"""
import base64
import unittest

import cv2
import numpy as np

from app.fusion import dispatch
from app.fusion.contract import FusedSkill, Precondition, Step


def _png(img) -> str:
    return base64.b64encode(cv2.imencode(".png", img)[1]).decode()


# A screenshot that is flat grey except for one distinctive textured "button" patch at a known box.
_W, _H = 1280, 720
_rng = np.random.default_rng(7)
_SHOT = np.full((_H, _W, 3), 128, np.uint8)
_BX, _BY, _BW, _BH = 600, 300, 160, 90
_SHOT[_BY:_BY + _BH, _BX:_BX + _BW] = _rng.integers(0, 255, (_BH, _BW, 3), dtype=np.uint8)
_SHOT_BYTES = cv2.imencode(".png", _SHOT)[1].tobytes()

_BUTTON_CROP = _png(_SHOT[_BY:_BY + _BH, _BX:_BX + _BW])                       # matches the shot
_WRONG_CROP = _png(_rng.integers(0, 255, (_BH, _BW, 3), dtype=np.uint8))        # a DIFFERENT texture -> misses
# normalized coords of the button centre — where a re-grounding model would click
_NX = round((_BX + _BW // 2) / _W * 999)
_NY = round((_BY + _BH // 2) / _H * 999)


class _FakeExec:
    surface = "browser"

    def screenshot(self) -> bytes:
        return _SHOT_BYTES

    def click_at(self, x, y, action="click", args=None) -> dict:
        return {"ok": True, "x": x, "y": y}

    def fire_keyboard(self, op, args=None) -> dict:
        return {"ok": True}

    def settle(self, timeout=3.0) -> None:
        pass


class _Verifier:
    def __init__(self, ok):
        self.ok = ok

    def check(self, skill) -> bool:
        return self.ok


def _skill(crop_b64: str) -> FusedSkill:
    return FusedSkill(
        name="t", surface="browser", target="", verify={"kind": "checker", "checker": "x"},
        steps=[Step(intent="click the button", primitive="click", args={"x": _NX, "y": _NY},
                    pre=Precondition(crop_b64=crop_b64, settle=False))],
    )


class SelfHealPersistTests(unittest.TestCase):
    def _mock_escalate(self):
        return lambda ex, step: {"ok": True, "_heal_coords": (_NX, _NY), "_heal_shot": _SHOT_BYTES}

    def test_drift_heals_once_then_next_replay_is_0cu(self):
        skill = _skill(_WRONG_CROP)                      # crop is stale -> will miss
        orig = dispatch._escalate
        dispatch._escalate = self._mock_escalate()
        try:
            r1 = dispatch.replay(skill, _FakeExec(), _Verifier(True), heal=True)
        finally:
            dispatch._escalate = orig
        self.assertTrue(r1["verified"])
        self.assertEqual(r1["cu_calls"], 1)              # crop miss -> ONE escalation
        self.assertEqual(r1["healed"], [0])              # step 0 re-cut + persisted

        # the SAME skill, now carrying the re-cut crop, matches on the live screen -> 0 CU
        r2 = dispatch.replay(skill, _FakeExec(), _Verifier(True), heal=True)
        self.assertTrue(r2["verified"])
        self.assertEqual(r2["cu_calls"], 0)              # durable: drift paid ONCE, then free

    def test_unverified_heal_is_not_persisted(self):
        skill = _skill(_WRONG_CROP)
        orig = dispatch._escalate
        dispatch._escalate = self._mock_escalate()
        try:
            r = dispatch.replay(skill, _FakeExec(), _Verifier(False), heal=True)   # run does NOT verify
        finally:
            dispatch._escalate = orig
        self.assertFalse(r["verified"])
        self.assertEqual(r["healed"], [])                # a re-ground that didn't achieve the goal
        self.assertEqual(skill.steps[0].pre.crop_b64, _WRONG_CROP)   # ...never poisons the skill

    def test_heal_off_by_default_leaves_skill_untouched(self):
        skill = _skill(_WRONG_CROP)
        orig = dispatch._escalate
        dispatch._escalate = self._mock_escalate()
        try:
            r = dispatch.replay(skill, _FakeExec(), _Verifier(True))   # heal defaults False
        finally:
            dispatch._escalate = orig
        self.assertEqual(r["healed"], [])
        self.assertEqual(skill.steps[0].pre.crop_b64, _WRONG_CROP)

    def test_persisted_heal_survives_store_roundtrip(self):
        """The DURABLE claim, end to end: a heal saved to the store and reloaded FROM DISK still
        replays at 0 CU. Self-improvement persists across a process restart, not just within one
        in-memory object — which is the whole point of 'memory that doesn't rot'."""
        import tempfile
        from app.fusion.skill_store import FusionSkillStore
        skill = _skill(_WRONG_CROP)
        orig = dispatch._escalate
        dispatch._escalate = self._mock_escalate()
        try:
            r1 = dispatch.replay(skill, _FakeExec(), _Verifier(True), heal=True)
        finally:
            dispatch._escalate = orig
        self.assertEqual(r1["healed"], [0])

        with tempfile.TemporaryDirectory() as root:
            store = FusionSkillStore(root)
            store.save_promoted(skill, verified=True, cu_calls=r1["cu_calls"], reason="self-heal")
            reloaded = store.load_active("t")                  # a fresh object, parsed from JSON on disk
        self.assertIsNotNone(reloaded)
        self.assertNotEqual(reloaded.steps[0].pre.crop_b64, _WRONG_CROP)   # the re-cut crop was persisted

        r2 = dispatch.replay(reloaded, _FakeExec(), _Verifier(True), heal=True)
        self.assertEqual(r2["cu_calls"], 0)                    # reloaded from disk -> still 0 CU


if __name__ == "__main__":
    unittest.main()
