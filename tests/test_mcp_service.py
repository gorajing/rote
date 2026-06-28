import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.local_skill_registry import LocalSkillRegistry
from app.mcp_service import MCPService, ServiceError
from app.skill_search_index import build_documents, skill_document
from database import api as database_api


def macro(name="demo", version=1, surface="desktop", status="active"):
    return {
        "schema_version": 2, "surface": surface, "name": name, "app": "TextEdit",
        "version": version, "parent_version": None, "status": status,
        "note": "Create a verified text document", "params": {"text": "hello"},
        "checker": {"type": "condition", "condition": {}}, "stats": {},
        "steps": [{
            "id": "type_text", "op": "type" if surface == "desktop" else "fill",
            **({"text": "{{text}}"} if surface == "desktop" else {
                "target": {"label": "Text"}, "text": "{{text}}",
            }),
            "precondition": {}, "postcondition": {}, "timeout": 1,
            "retry_limit": 0, "fallback": [], "why": "type text",
        }],
    }


class MCPServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "demo.macro.json").write_text(json.dumps(macro()), encoding="utf-8")
        self.registry = LocalSkillRegistry(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_registry_lists_selected_desktop_skills(self):
        listed = self.registry.list_skills(surface="desktop")
        self.assertEqual([(item["name"], item["version"]) for item in listed], [("demo", 1)])

    def test_search_applies_filters_and_resolves_exact_local_version(self):
        calls = []

        def searcher(query, **kwargs):
            calls.append((query, kwargs))
            return [
                {"skill_name": "demo", "version": 1, "score": 0.9, "description": "match"},
                {"skill_name": "demo", "version": 99, "score": 0.8},
            ]

        result = MCPService(self.registry, searcher=searcher).search_skills("write text", "TextEdit", 3)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["skills"][0]["version"], 1)
        self.assertEqual(calls[0][1]["filters"]["doc_type"], "executable_skill")
        self.assertEqual(calls[0][1]["filters"]["app"], "TextEdit")

    def test_replay_requires_confirmation_and_rejects_unknown_params(self):
        service = MCPService(self.registry, replayer=lambda *args, **kwargs: {"success": True})
        with self.assertRaises(ServiceError) as error:
            service.replay_skill("demo", 1)
        self.assertEqual(error.exception.code, "confirmation_required")
        with self.assertRaises(ServiceError) as error:
            service.replay_skill("demo", 1, {"unknown": "x"}, True)
        self.assertEqual(error.exception.code, "invalid_params")

    def test_replay_records_result(self):
        service = MCPService(self.registry, replayer=lambda *args, **kwargs: {
            "success": True, "elapsed_s": 0.1, "model_calls": 0, "failed_step_id": None,
        })
        result = service.replay_skill("demo", 1, {"text": "updated"}, True)
        self.assertTrue(result["result"]["success"])
        self.assertEqual(self.registry.load_skill("demo")["stats"]["uses"], 1)

    def test_replay_lock_returns_busy(self):
        service = MCPService(self.registry, replayer=lambda *args, **kwargs: {"success": True})
        service._replay_lock.acquire()
        try:
            with self.assertRaises(ServiceError) as error:
                service.replay_skill("demo", 1, confirm_execution=True)
            self.assertEqual(error.exception.code, "busy")
        finally:
            service._replay_lock.release()

    def test_search_index_projects_only_executable_desktop_skills(self):
        document = skill_document(macro())
        self.assertEqual(document["_id"], "macro:demo:v1")
        self.assertTrue(document["checker_verified"])
        self.assertIsNone(skill_document(macro(surface="browser")))
        self.assertIsNone(skill_document(macro(status="candidate")))
        self.assertIsNone(skill_document(macro(name="stale_demo")))
        self.assertEqual(len(build_documents(self.registry)), 1)


class DatabaseAPITests(unittest.TestCase):
    def test_retrieve_builds_filtered_vector_pipeline(self):
        class Collection:
            def aggregate(self, pipeline):
                self.pipeline = pipeline
                return [{"skill_name": "demo"}]

        collection = Collection()
        with patch.object(database_api, "_embed", return_value=[0.1]), \
             patch.object(database_api, "_collection", return_value=collection):
            result = database_api.retrieve("write text", 5, {
                "surface": "desktop", "status": "active",
            })
        self.assertEqual(result[0]["skill_name"], "demo")
        vector = collection.pipeline[0]["$vectorSearch"]
        self.assertEqual(vector["filter"], {"$and": [
            {"surface": "desktop"}, {"status": "active"},
        ]})
        self.assertEqual(collection.pipeline[-1], {"$project": {"embedding": 0}})

    def test_retrieve_validates_query_and_limit(self):
        with self.assertRaises(ValueError):
            database_api.retrieve("", 5)
        with self.assertRaises(ValueError):
            database_api.retrieve("query", 51)


class FastMCPContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_exposes_expected_tools_in_memory(self):
        from fastmcp import Client
        from app.mcp_server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
        self.assertEqual({tool.name for tool in tools}, {
            "search_skills", "list_skills", "get_skill", "get_skill_history", "replay_skill",
        })


if __name__ == "__main__":
    unittest.main()
