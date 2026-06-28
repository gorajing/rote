"""Hermetic tests for the fusion skill store — the cross-run 'it remembers' persistence.

Covers the three regressions that matter: success-gating (never promote an unverified skill),
crop-preserving roundtrip (FusedSkill survives store->load intact, including spatial crops),
and versioning (a second promote bumps the version and supersedes the previous active).
No arena, no Gemini, no network.
"""
import base64
import tempfile
import unittest
from pathlib import Path

from app.fusion.contract import FusedSkill, Precondition, Step
from app.fusion.skill_store import FusionSkillStore

_CROP = base64.b64encode(b"fake-png-bytes-for-the-crop").decode()


def _skill(name="dispute", version=1):
    return FusedSkill(
        name=name, surface="browser", target="http://localhost:8800/billing",
        params={"customer": "Acme Corp"}, verify={"kind": "checker", "checker": "dispute_workflow"},
        version=version,
        steps=[
            Step("click the Acme row", "click", {"x": 100, "y": 200}, pre=Precondition(crop_b64=_CROP)),
            Step("type the note", "type", {"text": "duplicate charge"}),
        ],
    )


class TestFusionSkillStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = FusionSkillStore(root=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_promote_is_success_gated(self):
        with self.assertRaises(ValueError):
            self.store.save_promoted(_skill(), verified=False)
        self.assertIsNone(self.store.load_active("dispute"))  # nothing persisted on refusal

    def test_save_then_load_roundtrip_preserves_crops(self):
        rec = self.store.save_promoted(_skill(version=1), verified=True, cu_calls=0)
        self.assertEqual(rec["status"], "active")
        self.assertEqual(rec["version"], 2)  # bumped from the parent (1)

        got = self.store.load_active("dispute")
        self.assertIsNotNone(got)
        self.assertEqual(got.name, "dispute")
        self.assertEqual(got.surface, "browser")
        self.assertEqual(len(got.steps), 2)
        self.assertEqual(got.steps[0].primitive, "click")
        self.assertEqual(got.steps[0].args, {"x": 100, "y": 200})
        self.assertEqual(got.steps[0].pre.crop_b64, _CROP)          # crop preserved
        self.assertEqual(got.steps[1].primitive, "type")
        self.assertEqual(got.steps[1].args["text"], "duplicate charge")
        self.assertIsNone(got.steps[1].pre)

    def test_second_promote_bumps_and_supersedes(self):
        self.store.save_promoted(_skill(version=1), verified=True)   # -> v2 active
        v2 = self.store.load_active("dispute")
        self.store.save_promoted(v2, verified=True)                  # -> v3 active, v2 superseded
        status = {h["version"]: h["status"] for h in self.store.history("dispute")}
        self.assertEqual(status.get(3), "active")
        self.assertEqual(status.get(2), "superseded")
        self.assertEqual(self.store.load_active("dispute").version, 3)

    def test_settle_false_roundtrips(self):
        s = _skill()
        s.steps[0].pre = Precondition(crop_b64=_CROP, settle=False)
        self.store.save_promoted(s, verified=True)
        self.assertFalse(self.store.load_active("dispute").steps[0].pre.settle)

    def test_skill_name_matches_compiler_default(self):
        # The warm-load demo keys on f"fused_{GOAL.id}"; the compiler defaults to f"fused_{task_id}".
        # If either drifts, the "remembers" demo silently never warms — assert they agree.
        from app.fusion.validate_memory import SKILL_NAME
        from app.fusion.validate_selfheal import GOAL
        self.assertEqual(SKILL_NAME, f"fused_{GOAL.id}")

    def test_ikjun_get_history_survives_a_fusion_file(self):
        # ikjun's get_history globs v*.json; our fusion-v{N}.json must not match it even in a
        # shared <name>/ folder, so his code can't crash on int("2.fusion").
        from app.local_skill_registry import LocalSkillRegistry
        root = Path(self._tmp.name)
        his = LocalSkillRegistry(root=root)               # his store = root/registry
        mine = FusionSkillStore(root=root / "registry")   # shares the same <name>/ folder
        mine.save_promoted(_skill(name="shared_skill"), verified=True)
        self.assertIsInstance(his.get_history("shared_skill"), list)  # must not raise


if __name__ == "__main__":
    unittest.main()
