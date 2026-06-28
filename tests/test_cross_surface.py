import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.local_skill_registry import LocalSkillRegistry
from app.macro_skill import validate_macro
from app.skill_repair import _clean_steps
from app.verification import check_final, evaluate_condition
from app.verified_replay import replay_verified


class BrowserStateBackend:
    def __init__(self):
        self.state = {"url": "about:blank", "title": "", "visible_text": ""}

    def inspect(self):
        return dict(self.state)

    def execute(self, step):
        if step["op"] == "navigate":
            self.state.update(url=step["url"], title="Settings", visible_text="Billing email Save Settings")
        elif step["op"] == "fill":
            self.state["visible_text"] += " " + step["text"]
        elif step["op"] == "click":
            self.state["visible_text"] += " Settings updated"
        return {}


def browser_skill():
    return {
        "schema_version": 2, "surface": "browser", "name": "browser_demo", "app": "Browser",
        "os": "any", "version": 1, "parent_version": None, "status": "active",
        "params": {"email": "new@example.com"},
        "checker": {"type": "condition", "condition": {"text_contains": "Settings updated"}},
        "stats": {},
        "steps": [
            {"id": "open", "op": "navigate", "url": "https://example.test/settings",
             "precondition": {}, "postcondition": {"url_contains": "/settings"},
             "timeout": 1, "retry_limit": 0, "fallback": [], "why": "open"},
            {"id": "fill", "op": "fill", "target": {"label": "Billing email"}, "text": "{{email}}",
             "precondition": {"element_visible": "Billing email"}, "postcondition": {},
             "timeout": 1, "retry_limit": 0, "fallback": [], "why": "fill"},
            {"id": "save", "op": "click", "target": {"role": "button", "name": "Save Settings"},
             "precondition": {"element_visible": "Save Settings"},
             "postcondition": {"text_contains": "Settings updated"},
             "timeout": 1, "retry_limit": 0, "fallback": [], "why": "save"},
        ],
    }


class CrossSurfaceTests(unittest.TestCase):
    def test_browser_macro_requires_semantic_targets(self):
        skill = browser_skill()
        validate_macro(skill)
        skill["steps"][1]["target"] = {"x": 10}
        with self.assertRaises(ValueError):
            validate_macro(skill)

    def test_browser_repair_requires_semantic_targets(self):
        with self.assertRaises(ValueError):
            _clean_steps({"replacement_steps": [{"op": "click", "target": {"x": 10}}]}, {}, surface="browser")
        repaired = _clean_steps(
            {"replacement_steps": [{"op": "click", "target": {"role": "button", "name": "Save"}}]},
            {}, surface="browser",
        )
        self.assertEqual(repaired[0]["target"]["name"], "Save")

    def test_generic_condition_composition(self):
        state = {"url": "https://example.test/settings", "visible_text": "Saved", "title": "Settings"}
        condition = {"all": [
            {"url_contains": "/settings"},
            {"any": [{"text_contains": "Saved"}, {"text_contains": "Failed"}]},
            {"not": {"title": "Error"}},
        ]}
        self.assertTrue(evaluate_condition(condition, state, {})[0])

    def test_shared_replay_engine_runs_browser_skill(self):
        with tempfile.TemporaryDirectory() as folder:
            result = replay_verified(
                browser_skill(), backend=BrowserStateBackend(), registry=LocalSkillRegistry(folder),
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["steps"], 3)
        self.assertEqual(result["model_calls"], 0)

    def test_http_json_checker(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self): return json.dumps({"settings": {"email": "new@example.com"}}).encode()

        with patch("urllib.request.urlopen", return_value=Response()):
            passed, failures = check_final(
                {"type": "http_json", "url": "https://example.test/state",
                 "equals": {"settings.email": "new@example.com"}}, {}, {},
            )
        self.assertTrue(passed, failures)

    def test_registry_discovers_skill_by_declared_name(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "different_filename.macro.json"
            path.write_text(json.dumps(browser_skill()), encoding="utf-8")
            loaded = LocalSkillRegistry(folder).load_skill("browser_demo")
        self.assertEqual(loaded["surface"], "browser")


if __name__ == "__main__":
    unittest.main()
