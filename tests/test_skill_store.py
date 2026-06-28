"""Hermetic tests for the MongoDB-backed skill store (database.api mocked — no network).

Covers the logic that decides what counts as a replayable match, the search threshold/ordering, and
the shape of a document pushed to the `tasks` collection.
"""
import unittest
from unittest import mock

from app import skill_store


def _skill_doc(name, desc, score):
    macro = {"schema_version": 2, "name": name, "surface": "desktop",
             "checker": {"type": "word_docx"}, "params": {}, "steps": [{"id": "s1", "op": "type", "text": "hi"}]}
    return {"name": name, "description": desc, "surface": "desktop", "macro": macro, "score": score}


class DocToMacroTests(unittest.TestCase):
    def test_full_macro_doc_is_replayable(self):
        macro = skill_store._doc_to_macro(_skill_doc("calc", "calculate", 0.9))
        self.assertIsNotNone(macro)
        self.assertEqual(macro["name"], "calc")

    def test_top_level_macro_steps_are_wrapped(self):
        doc = {"name": "x", "surface": "desktop", "steps": [{"op": "open_app", "app": "Word"}]}
        macro = skill_store._doc_to_macro(doc)
        self.assertIsNotNone(macro)
        self.assertEqual(macro["steps"][0]["op"], "open_app")

    def test_foreign_trace_is_not_replayable(self):
        # the teammate's execution_trace docs have no macro and non-macro steps
        doc = {"doc_type": "execution_trace", "description": "send an email",
               "steps": [{"turn": 1, "action": "click", "args": {}}]}
        self.assertIsNone(skill_store._doc_to_macro(doc))


class SearchTests(unittest.TestCase):
    def test_returns_best_replayable_above_threshold(self):
        hits = [_skill_doc("calc", "calculate", 0.88), _skill_doc("notes", "notes", 0.83)]
        with mock.patch.object(skill_store.api, "retrieve", return_value=hits):
            macro = skill_store.search("do a calculation", threshold=0.82)
        self.assertEqual(macro["name"], "calc")

    def test_below_threshold_returns_none(self):
        hits = [_skill_doc("calc", "calculate", 0.79)]
        with mock.patch.object(skill_store.api, "retrieve", return_value=hits):
            self.assertIsNone(skill_store.search("order a pizza", threshold=0.82))

    def test_skips_unreplayable_hit_even_if_high_score(self):
        trace = {"doc_type": "execution_trace", "description": "email",
                 "steps": [{"action": "click"}], "score": 0.95}
        with mock.patch.object(skill_store.api, "retrieve", return_value=[trace]):
            self.assertIsNone(skill_store.search("send email", threshold=0.82))

    def test_retrieve_error_is_swallowed(self):
        with mock.patch.object(skill_store.api, "retrieve", side_effect=RuntimeError("no index")):
            self.assertIsNone(skill_store.search("anything"))


class FlattenTests(unittest.TestCase):
    CHILD = {"schema_version": 2, "name": "save_doc", "surface": "desktop", "checker": {},
             "params": {"filename": "document"},
             "steps": [{"id": "open_save", "op": "hotkey", "keys": ["command", "s"]},
                       {"id": "type_name", "op": "type", "text": "{{filename}}"}]}
    PARENT = {"schema_version": 2, "name": "calc", "surface": "desktop", "checker": {},
              "params": {"filename": "calc_result", "calculation": "1"},
              "steps": [{"id": "do_calc", "op": "type", "text": "{{calculation}}"},
                        {"id": "save", "op": "call", "skill": "save_doc", "params": {"filename": "{{filename}}"}}]}

    class _Reg:
        def load_skill(self, name, version=None):
            return dict(FlattenTests.CHILD)

    def test_no_call_steps_remain(self):
        flat = skill_store.flatten_macro(self.PARENT, registry=self._Reg())
        self.assertNotIn("call", [s["op"] for s in flat["steps"]])

    def test_subskill_steps_are_inlined_with_unique_ids(self):
        flat = skill_store.flatten_macro(self.PARENT, registry=self._Reg())
        ids = [s["id"] for s in flat["steps"]]
        self.assertEqual(len(ids), len(set(ids)))                 # no duplicate ids
        self.assertTrue(any(i.startswith("save__") for i in ids)) # inlined under the call id

    def test_placeholders_preserved_through_flatten(self):
        flat = skill_store.flatten_macro(self.PARENT, registry=self._Reg())
        texts = [s.get("text") for s in flat["steps"] if s["op"] == "type"]
        self.assertIn("{{calculation}}", texts)   # top-level placeholder kept
        self.assertIn("{{filename}}", texts)       # passed through the call, kept parameterizable

    def test_flat_macro_is_valid_for_replay(self):
        from app.macro_skill import migrate_macro
        flat = skill_store.flatten_macro(self.PARENT, registry=self._Reg())
        migrate_macro(flat)                        # must not raise (ids unique, ops allowed)


class SaveSkillTests(unittest.TestCase):
    def test_pushes_self_contained_document(self):
        macro = {"schema_version": 2, "name": "calc", "surface": "desktop",
                 "checker": {"type": "word_docx"}, "params": {"x": "1"},
                 "steps": [{"id": "s1", "op": "type", "text": "hi"}]}
        captured = {}
        with mock.patch.object(skill_store.api, "push", side_effect=lambda d: captured.update(d) or "id1"):
            skill_id = skill_store.save_skill(macro, "calculate two numbers", "calc")
        self.assertEqual(skill_id, "id1")
        self.assertEqual(captured["doc_type"], "skill")
        self.assertEqual(captured["description"], "calculate two numbers")
        self.assertEqual(captured["macro"]["name"], "calc")
        self.assertEqual(captured["variables"], {"x": "1"})
        self.assertIn("created_at", captured)


if __name__ == "__main__":
    unittest.main()
