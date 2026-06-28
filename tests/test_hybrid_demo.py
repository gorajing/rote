import unittest

from decimal import Decimal

from app.hybrid_demo import (
    LOCAL_APP_URL,
    build_calculator_credit_skill,
    build_invoice_note_skill,
    credit_note,
    prepare_browser_skill,
    run_desktop_app,
)
from app.local_skill_registry import LocalSkillRegistry
from app.macro_skill import validate_macro


class FakeDesktopBackend:
    def __init__(self, state):
        self.state = state
        self.steps = []

    def execute(self, step):
        self.steps.append(step)
        return {"message": "opened"}

    def inspect(self):
        return dict(self.state)


class HybridDemoTests(unittest.TestCase):
    def test_prepare_browser_skill_rewrites_localhost_urls_without_mutating_source(self):
        skill = LocalSkillRegistry().load_skill("acme_settings_email")
        prepared = prepare_browser_skill(skill, "http://127.0.0.1:8899/")

        self.assertEqual(skill["start_url"], f"{LOCAL_APP_URL}/settings")
        self.assertEqual(prepared["start_url"], "http://127.0.0.1:8899/settings")
        self.assertEqual(prepared["reset"]["url"], "http://127.0.0.1:8899/reset?variant=baseline")
        self.assertEqual(prepared["checker"]["url"], "http://127.0.0.1:8899/state")
        self.assertEqual(prepared["steps"][0]["url"], "http://127.0.0.1:8899/settings")

    def test_run_desktop_app_verifies_running_app(self):
        backend = FakeDesktopBackend({
            "foreground_app": "Calculator",
            "running_apps": ["Calculator", "Finder"],
        })
        result = run_desktop_app("Calculator", backend=backend, launch_wait=1.5)

        self.assertTrue(result["ok"])
        self.assertTrue(result["focused"])
        self.assertEqual(backend.steps[0]["op"], "open_app")
        self.assertEqual(backend.steps[0]["app"], "Calculator")

    def test_run_desktop_app_reports_unverified_app(self):
        backend = FakeDesktopBackend({
            "foreground_app": "Finder",
            "running_apps": ["Finder"],
        })
        result = run_desktop_app("Calculator", backend=backend)

        self.assertFalse(result["ok"])
        self.assertFalse(result["running"])

    def test_build_calculator_credit_skill_is_valid_desktop_macro(self):
        skill, calculation = build_calculator_credit_skill(Decimal("1250.00"), Decimal("0.10"))

        validate_macro(skill)
        self.assertEqual(calculation, "1250*0.1")
        self.assertEqual(skill["params"]["expected_result"], "125")
        self.assertEqual(skill["checker"]["condition"]["clipboard_contains"], "{{expected_result}}")

    def test_credit_note_formats_calculator_clipboard_value(self):
        self.assertEqual(
            credit_note("inv-001", "125"),
            "Credit calculated in Calculator for inv-001: $125.00",
        )
        self.assertEqual(
            credit_note("inv-002", "1,234.5"),
            "Credit calculated in Calculator for inv-002: $1,234.50",
        )

    def test_credit_note_rejects_non_numeric_clipboard(self):
        with self.assertRaises(ValueError):
            credit_note("inv-001", "not a number")

    def test_build_invoice_note_skill_is_valid_browser_macro_with_state_checker(self):
        skill = build_invoice_note_skill(
            "http://127.0.0.1:8899/",
            "inv-001",
            0,
            "Credit calculated in Calculator for inv-001: $125.00",
        )

        validate_macro(skill)
        self.assertEqual(skill["start_url"], "http://127.0.0.1:8899/invoice/inv-001/note")
        self.assertEqual(skill["checker"]["url"], "http://127.0.0.1:8899/state")
        self.assertEqual(skill["checker"]["equals"]["invoices.0.note"], "{{note}}")
        self.assertEqual(skill["steps"][1]["target"], {"label": "Invoice Note"})


if __name__ == "__main__":
    unittest.main()
