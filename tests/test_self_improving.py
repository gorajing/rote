import copy
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.local_skill_registry import LocalSkillRegistry
from app.macro_skill import migrate_macro, resolve_params
from app.skill_repair import RepairService, _clean_steps
from app.verified_replay import check_final, replay_verified


class FakeBackend:
    def __init__(self, create_document=True):
        self.state = {"foreground_app": "", "windows": "", "ui_text": "", "word_document_count": 0}
        self.executed = []
        self.create_document = create_document

    def inspect(self):
        return copy.deepcopy(self.state)

    def execute(self, step):
        self.executed.append(step["id"] if "id" in step else step["op"])
        if step["op"] == "open_app":
            self.state["foreground_app"] = step["app"]
            self.state["windows"] = step["app"]
        elif step["op"] == "hotkey" and step.get("keys") == ["command", "n"] and self.create_document:
            self.state["word_document_count"] = 1
        return {}

    def screenshot_png(self):
        return b"fake-png"


class FakeModels:
    class Response:
        text = json.dumps({
            "replacement_steps": [{
                "op": "hotkey", "keys": ["command", "n"],
                "postcondition": {"word_document": True},
                "why": "Create the missing blank document",
            }]
        })

    def generate_content(self, **kwargs):
        return self.Response()


class FakeClient:
    models = FakeModels()


class BrokenModels:
    def generate_content(self, **kwargs):
        raise RuntimeError("model unavailable")


class BrokenClient:
    models = BrokenModels()


def base_skill(name="demo"):
    return {
        "schema_version": 2, "name": name, "app": "Microsoft Word", "os": "macos",
        "version": 1, "parent_version": None, "status": "active", "params": {},
        "checker": {}, "stats": {},
        "steps": [{
            "id": "verify_document", "op": "wait", "seconds": 0,
            "precondition": {}, "postcondition": {"word_document": True},
            "timeout": 0, "retry_limit": 0, "fallback": [], "why": "Verify document",
        }],
    }


class MacroTests(unittest.TestCase):
    def test_legacy_migration_is_non_mutating_and_parameterized(self):
        legacy = {
            "name": "legacy", "params": {"filename": "gemini"},
            "steps": [{"op": "type", "text": "save gemini", "why": "name"}],
        }
        migrated = migrate_macro(legacy)
        self.assertNotIn("schema_version", legacy)
        self.assertEqual(migrated["steps"][0]["text"], "save {{filename}}")
        self.assertEqual(resolve_params(migrated["steps"][0]["text"], {"filename": "report"}), "save report")
        self.assertEqual(migrated["steps"][0]["id"], "type_1")

    def test_replay_stops_at_failed_step(self):
        skill = base_skill()
        skill["steps"].append({
            "id": "must_not_run", "op": "type", "text": "no", "precondition": {},
            "postcondition": {}, "timeout": 0, "retry_limit": 0, "fallback": [], "why": "no",
        })
        backend = FakeBackend(create_document=False)
        result = replay_verified(skill, backend=backend, registry=LocalSkillRegistry(tempfile.mkdtemp()))
        self.assertFalse(result["success"])
        self.assertEqual(result["failed_step_id"], "verify_document")
        self.assertEqual(backend.executed, ["verify_document"])

    def test_retry_and_fallback_are_counted(self):
        skill = base_skill()
        skill["steps"][0]["retry_limit"] = 1
        skill["steps"][0]["fallback"] = [{"op": "hotkey", "keys": ["command", "n"]}]
        backend = FakeBackend(create_document=True)
        result = replay_verified(skill, backend=backend, registry=LocalSkillRegistry(tempfile.mkdtemp()))
        self.assertTrue(result["success"])
        self.assertEqual(result["retries"], 1)
        self.assertEqual(result["fallbacks"], 1)

    def test_repair_rejects_coordinates_and_hardcoded_params(self):
        with self.assertRaises(ValueError):
            _clean_steps({"replacement_steps": [{"op": "type", "x": 2, "text": "{{text}}"}]}, {"text": "hello"})
        with self.assertRaises(ValueError):
            _clean_steps({"replacement_steps": [{"op": "type", "text": "hello"}]}, {"text": "hello"})

    def test_word_checker_reads_docx_content(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", "<w:document><w:t>verified text</w:t></w:document>")
            passed, failures = check_final(
                {"type": "word_docx", "location": folder, "filename": "report.docx",
                 "contains": ["verified", "text"]},
                {},
            )
            self.assertTrue(passed, failures)
            failed, _ = check_final(
                {"type": "word_docx", "location": folder, "filename": "report.docx", "contains": "wrong"},
                {},
            )
            self.assertFalse(failed)


class RegistryAndRepairTests(unittest.TestCase):
    def test_candidate_is_promoted_only_after_full_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry = LocalSkillRegistry(root)
            skill = base_skill()
            (root / "demo.macro.json").write_text(json.dumps(skill), encoding="utf-8")
            backend = FakeBackend(create_document=True)

            def reset(_params):
                backend.state["word_document_count"] = 0

            service = RepairService(registry=registry, reset=reset, client=FakeClient())
            first = replay_verified(skill, backend=backend, registry=registry)
            self.assertFalse(first["success"])
            repaired = service.repair_and_validate(skill, {}, first, backend=backend)
            self.assertTrue(repaired["success"])
            self.assertTrue(repaired["promoted"])
            self.assertEqual(registry.load_skill("demo")["version"], 2)
            self.assertEqual(registry.load_skill("demo")["stats"]["successes"], 1)
            history = registry.get_history("demo")
            self.assertEqual([item["status"] for item in history], ["superseded", "active"])

    def test_model_failure_leaves_active_skill_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry = LocalSkillRegistry(root)
            skill = base_skill()
            (root / "demo.macro.json").write_text(json.dumps(skill), encoding="utf-8")
            backend = FakeBackend(create_document=False)
            service = RepairService(registry=registry, reset=lambda params: None, client=BrokenClient())
            result = replay_verified(
                skill, allow_repair=True, backend=backend, registry=registry, repair_service=service,
            )
            self.assertFalse(result["success"])
            self.assertFalse(result["promoted"])
            self.assertIn("model unavailable", result["repair_error"])
            self.assertEqual(registry.load_skill("demo")["version"], 1)

    def test_rejected_candidate_does_not_replace_active(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = LocalSkillRegistry(folder)
            skill = base_skill()
            candidate = registry.create_candidate(skill)
            registry.reject(candidate, "checker failed")
            self.assertEqual(registry.get_history("demo")[0]["status"], "rejected")
            with self.assertRaises(FileNotFoundError):
                registry.load_skill("demo")

    def test_run_metrics_are_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = LocalSkillRegistry(folder)
            skill = base_skill()
            recorded = registry.record_run(skill, {"success": False, "elapsed_s": 2.5, "model_calls": 0,
                                                    "failed_step_id": "verify_document"})
            self.assertEqual(recorded["stats"]["uses"], 1)
            self.assertEqual(recorded["stats"]["failures"], 1)
            self.assertEqual(registry.load_skill("demo")["last_run"]["failed_step_id"], "verify_document")

    def test_promoted_subskill_transfers_to_another_workflow(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry = LocalSkillRegistry(root)
            child = base_skill("shared_prepare")
            parent_template = {
                "schema_version": 2, "app": "Microsoft Word", "os": "macos", "version": 1,
                "parent_version": None, "status": "active", "params": {}, "checker": {}, "stats": {},
                "steps": [{
                    "id": "prepare", "op": "call", "skill": "shared_prepare", "params": {},
                    "precondition": {}, "postcondition": {}, "timeout": 0,
                    "retry_limit": 0, "fallback": [], "why": "shared preparation",
                }],
            }
            first_parent = {**parent_template, "name": "workflow_a"}
            second_parent = {**parent_template, "name": "workflow_b"}
            for value in (child, first_parent, second_parent):
                (root / f"{value['name']}.macro.json").write_text(json.dumps(value), encoding="utf-8")
            backend = FakeBackend(create_document=True)

            def reset(_params):
                backend.state["word_document_count"] = 0

            failure = replay_verified(first_parent, backend=backend, registry=registry)
            service = RepairService(registry=registry, reset=reset, client=FakeClient())
            repaired = service.repair_and_validate(first_parent, {}, failure, backend=backend)
            self.assertTrue(repaired["promoted"])
            self.assertEqual(repaired["promoted_skill"], "shared_prepare")
            reset({})
            transferred = replay_verified(second_parent, backend=backend, registry=registry)
            self.assertTrue(transferred["success"])
            self.assertEqual(transferred["model_calls"], 0)


if __name__ == "__main__":
    unittest.main()
