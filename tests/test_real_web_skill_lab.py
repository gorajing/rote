import json
import tempfile
import unittest
from pathlib import Path

from app.real_web_skill_lab import (
    PageSnapshot,
    build_browser_skill,
    build_hybrid_skill,
    build_textedit_skill,
    note_text,
    save_hybrid_skill,
    validate_hybrid_skill,
)
from app.verification import evaluate_condition


SNAPSHOT = PageSnapshot(
    url="https://example.com/",
    title="Example Domain",
    heading="Example Domain",
    marker="rote-real-web-test",
)


class RealWebSkillLabTests(unittest.TestCase):
    def test_note_text_includes_source_title_and_marker(self):
        text = note_text(SNAPSHOT)
        self.assertIn("https://example.com/", text)
        self.assertIn("Example Domain", text)
        self.assertIn("rote-real-web-test", text)

    def test_browser_and_textedit_segments_are_valid_macros(self):
        browser = build_browser_skill(SNAPSHOT)
        desktop = build_textedit_skill(SNAPSHOT)
        self.assertEqual(browser["surface"], "browser")
        self.assertEqual(browser["steps"][0]["op"], "navigate")
        self.assertEqual(desktop["surface"], "desktop")
        self.assertEqual(desktop["steps"][0]["app"], "TextEdit")
        self.assertEqual(desktop["checker"]["condition"]["all"][1]["textedit_document_contains"], "{{marker}}")

    def test_hybrid_skill_validates_and_saves(self):
        skill = build_hybrid_skill(SNAPSHOT)
        validate_hybrid_skill(skill)
        with tempfile.TemporaryDirectory() as folder:
            path = save_hybrid_skill(skill, Path(folder) / "built.hybrid.json")
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["kind"], "hybrid_skill")
        self.assertIn("built_at", saved)
        self.assertNotIn("learned_at", saved)
        self.assertEqual(saved["segments"][0]["surface"], "browser")
        self.assertEqual(saved["segments"][1]["surface"], "desktop")

    def test_hybrid_skill_rejects_unknown_surface(self):
        skill = build_hybrid_skill(SNAPSHOT)
        skill["segments"][0]["surface"] = "phone"
        with self.assertRaises(ValueError):
            validate_hybrid_skill(skill)

    def test_textedit_condition_verifies_document_text(self):
        ok, failures = evaluate_condition(
            {"textedit_document_contains": "rote-real-web-test"},
            {"textedit_document_text": note_text(SNAPSHOT)},
            {},
        )
        self.assertTrue(ok, failures)


if __name__ == "__main__":
    unittest.main()
