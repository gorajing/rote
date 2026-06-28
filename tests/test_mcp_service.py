import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.local_skill_registry import LocalSkillRegistry
from rote_mcp.service import MCPService, ServiceError
from rote_mcp.descriptors import build_documents, fusion_skill_document, skill_document, sync_index
from rote_mcp.recall import SkillRecallService
from app.fusion.contract import FusedSkill, Step
from app.fusion.skill_store import FusionSkillStore
from rote_mcp.trace_memory import compact_trace_hint, intent_hash, trace_document
from rote_mcp.promotion import promote_trace
from rote_mcp import storage as mcp_storage
from rote_mcp.adaptive_store import AdaptiveSkillStore
from services.database_gateway import create_app as create_database_gateway
from database import api as database_api

try:                                   # fastmcp is a declared but optional dep (the MCP server lane)
    import fastmcp  # noqa: F401
    _HAS_FASTMCP = True
except ImportError:
    _HAS_FASTMCP = False


def macro(name="demo", version=1, surface="desktop", status="active"):
    return {
        "schema_version": 2, "surface": surface, "name": name, "app": "TextEdit",
        "version": version, "parent_version": None, "status": status,
        "note": "Create a verified text document", "params": {"text": "hello"},
        "checker": {"type": "condition", "condition": {}}, "stats": {},
        "last_run": {"success": True, "failed_step_id": None},
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

    def test_search_resolves_exact_local_version_without_app_exact_match(self):
        calls = []

        def searcher(query, **kwargs):
            calls.append((query, kwargs))
            return [
                {"skill_name": "demo", "version": 1, "score": 0.9, "description": "match"},
                {"skill_name": "demo", "version": 99, "score": 0.81},
                {"skill_name": "unrelated", "version": 1, "score": 0.79},
            ]

        result = MCPService(self.registry, searcher=searcher).search_skills("write text", "TextEdit", 3)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["skills"][0]["version"], 1)
        self.assertEqual(result["skills"][0]["variables"]["text"]["type"], "string")
        self.assertEqual(calls[0][1]["surface"], None)
        self.assertNotIn("filters", calls[0][1])
        self.assertNotIn("app", calls[0][1])

    def test_search_soft_re_ranks_same_app_skills_first(self):
        calc = macro(name="calc")
        calc["app"] = "Calculator"
        (self.root / "calc.macro.json").write_text(json.dumps(calc), encoding="utf-8")

        def searcher(query, **kwargs):
            return [
                {"skill_name": "demo", "version": 1, "score": 0.92},
                {"skill_name": "calc", "version": 1, "score": 0.85},
            ]

        service = MCPService(self.registry, searcher=searcher)
        biased = service.search_skills("do the thing", app="calculator", limit=5)
        self.assertEqual([item["name"] for item in biased["skills"]], ["calc", "demo"])
        unbiased = service.search_skills("do the thing", limit=5)
        self.assertEqual([item["name"] for item in unbiased["skills"]], ["demo", "calc"])

    def test_search_keeps_matches_above_relaxed_threshold(self):
        def searcher(query, **kwargs):
            return [{"skill_name": "demo", "version": 1, "score": 0.72}]

        result = MCPService(self.registry, searcher=searcher).search_skills("write", limit=5)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["skills"][0]["name"], "demo")

    def test_search_degrades_to_cu_when_searcher_unavailable(self):
        def searcher(query, **kwargs):
            raise RuntimeError("atlas down")

        recall = SkillRecallService(
            self.registry, atlas_searcher=searcher, cache_path=self.root / "missing-cache.json",
        )
        result = MCPService(self.registry, searcher=searcher, recall_service=recall).search_skills("write", limit=5)
        self.assertTrue(result["ok"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["next_action"], "execute_new_task")

    def test_search_empty_result_points_to_cu(self):
        result = MCPService(self.registry, searcher=lambda *a, **k: []).search_skills("write", limit=5)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["next_action"], "execute_new_task")

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

    def test_browser_replay_uses_generic_browser_runner(self):
        browser = macro(name="browser_demo", surface="browser")
        (self.root / "browser_demo.macro.json").write_text(json.dumps(browser), encoding="utf-8")
        calls = []

        def browser_replayer(skill, params, registry):
            calls.append((skill["name"], params["text"]))
            return {"success": True, "checker_passed": True, "elapsed_s": .1,
                    "model_calls": 0, "failed_step_id": None, "final_url": "https://example.test"}

        result = MCPService(self.registry, browser_replayer=browser_replayer).replay_skill(
            "browser_demo", 1, {"text": "value"}, True,
        )
        self.assertTrue(result["result"]["success"])
        self.assertEqual(calls, [("browser_demo", "value")])

    def test_replay_lock_returns_busy(self):
        service = MCPService(self.registry, replayer=lambda *args, **kwargs: {"success": True})
        service._desktop_lock.acquire()
        try:
            with self.assertRaises(ServiceError) as error:
                service.replay_skill("demo", 1, {"text": "value"}, confirm_execution=True)
            self.assertEqual(error.exception.code, "busy")
        finally:
            service._desktop_lock.release()

    def test_new_task_requires_confirmation_and_returns_unverified_trace(self):
        calls = []

        def computer_user(intent, **kwargs):
            calls.append((intent, kwargs))
            Path(kwargs["trace_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(kwargs["trace_path"]).write_text(json.dumps({
                "intent": intent,
                "steps": [
                    {"turn": 1, "action": "navigate", "intent": "open", "args": {}},
                    {"turn": 2, "action": "click", "intent": "select", "args": {}},
                ],
                "metrics": {"steps": 2, "final": "Done"},
            }))
            return {
                "steps": 2, "final": "Done", "termination_reason": "model_completed",
                "trace_path": kwargs["trace_path"],
            }

        pushed = []
        service = MCPService(
            self.registry,
            computer_user=computer_user,
            trace_searcher=lambda *args, **kwargs: [{
                "trace_id": "prior", "steps": [
                    {"action": "navigate", "intent": "Open YouTube", "args": {"url": "https://youtube.com", "x": 4}},
                ],
            }],
            trace_pusher=lambda document: pushed.append(document) or document["document_key"],
            skill_promoter=lambda *args, **kwargs: {"status": "rejected", "reason": "test"},
            recall_service=SkillRecallService(
                self.registry, adaptive_store=AdaptiveSkillStore(self.root / "adaptive"),
                atlas_searcher=lambda *args, **kwargs: [],
            ),
        )
        with self.assertRaises(ServiceError) as error:
            service.execute_new_task("Open YouTube")
        self.assertEqual(error.exception.code, "confirmation_required")
        with patch("rote_mcp.service.index_document", return_value="adaptive-id"):
            result = service.execute_new_task(
                "Open YouTube and search for hello", max_turns=7, confirm_execution=True,
                variables={"search_query": "hello"},
            )
        self.assertEqual(result["result"]["mode"], "computer_use")
        self.assertTrue(result["result"]["verified"])
        self.assertEqual(calls[0][1]["max_turns"], 7)
        self.assertTrue(calls[0][1]["trace_hint"])
        self.assertTrue(result["result"]["used_trace_hint"])
        self.assertEqual(result["result"]["source_trace_ids"], ["prior"])
        self.assertTrue(result["result"]["trace_persisted"])
        self.assertEqual(result["result"]["promotion"]["status"], "rejected")
        self.assertTrue(result["result"]["promotion"]["adaptive_skill"].startswith("adaptive_"))
        self.assertFalse(pushed[0]["verified"])
        self.assertNotIn("hello", json.dumps(pushed[0]))
        self.assertIn("{{search_query}}", pushed[0]["intent"])
        self.assertEqual(pushed[0]["variables"]["search_query"]["type"], "string")

    def test_new_task_continues_when_trace_memory_is_unavailable(self):
        def unavailable(*args, **kwargs):
            raise RuntimeError("index unavailable")

        def computer_user(intent, **kwargs):
            Path(kwargs["trace_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(kwargs["trace_path"]).write_text(json.dumps({
                "intent": intent, "steps": [], "metrics": {"steps": 0, "final": "Done"},
            }))
            return {"steps": 0, "final": "Done", "trace_path": kwargs["trace_path"]}

        service = MCPService(
            self.registry,
            computer_user=computer_user,
            trace_searcher=unavailable,
            trace_pusher=unavailable,
            skill_promoter=lambda *args, **kwargs: {"status": "compile_failed"},
            recall_service=SkillRecallService(
                self.registry, adaptive_store=AdaptiveSkillStore(self.root / "adaptive"),
                atlas_searcher=lambda *args, **kwargs: [],
            ),
        )
        with patch("rote_mcp.service.index_document", side_effect=RuntimeError("index unavailable")), \
             patch("rote_mcp.service.queue_pending"):
            result = service.execute_new_task("Open YouTube", confirm_execution=True)["result"]
        self.assertFalse(result["used_trace_hint"])
        self.assertFalse(result["trace_persisted"])
        self.assertIn("index unavailable", result["trace_persist_error"])

    def test_search_index_projects_strict_desktop_and_extended_browser_documents(self):
        document = skill_document(macro())
        self.assertEqual(document["doc_type"], "executable_skill")
        self.assertEqual(document["surface"], "desktop")
        self.assertTrue(document["checker_verified"])
        self.assertEqual(document["name"], "demo")
        self.assertEqual(document["steps"][0], {"op": "type", "why": "type text", "text": "{{text}}"})
        self.assertNotIn("hello", json.dumps(document))
        browser = skill_document(macro(surface="browser"))
        self.assertEqual(browser["surface"], "browser")
        self.assertTrue(browser["checker_verified"])
        self.assertIsNone(skill_document(macro(status="candidate")))
        self.assertIsNone(skill_document(macro(name="stale_demo")))
        self.assertEqual(
            len(build_documents(
                self.registry,
                fusion_store=type("EmptyFusion", (), {"list_active": lambda self: []})(),
                adaptive_store=AdaptiveSkillStore(self.root / "adaptive"),
            )),
            1,
        )

    def test_sync_writes_desktop_and_browser_skills_through_tasks_push(self):
        browser = macro(name="browser_demo", surface="browser")
        (self.root / "browser_demo.macro.json").write_text(json.dumps(browser), encoding="utf-8")
        pushed = []
        with patch(
            "rote_mcp.descriptors.push_document",
            side_effect=lambda doc: pushed.append(doc) or (doc.get("name") or doc["skill_name"]),
        ), patch(
            "rote_mcp.descriptors.prepare_document",
            side_effect=lambda doc: {**doc, "embedding": [0.1], "embedding_model": "test"},
        ), patch("rote_mcp.descriptors.SEMANTIC_CACHE", self.root / "semantic-cache.json"):
            ids = sync_index(
                self.registry,
                fusion_store=type("EmptyFusion", (), {"list_active": lambda self: []})(),
                adaptive_store=AdaptiveSkillStore(self.root / "adaptive"),
            )
        self.assertEqual(len(ids), 2)
        self.assertEqual({doc.get("name") or doc.get("skill_name") for doc in pushed}, {"demo", "browser_demo"})


class DatabaseAPITests(unittest.TestCase):
    def test_database_module_remains_original_public_contract(self):
        self.assertTrue(callable(database_api.push))
        self.assertTrue(callable(database_api.retrieve))
        self.assertFalse(hasattr(database_api, "push_trace"))
        self.assertFalse(hasattr(database_api, "retrieve_skill_documents"))

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

    def test_gateway_wraps_unchanged_push_and_retrieve(self):
        pushed = []
        app = create_database_gateway(
            push_fn=lambda doc: pushed.append(doc) or "mongo-id",
            retrieve_fn=lambda query, **kwargs: [{"skill_name": "demo", "score": .9}],
        )
        client = app.test_client()
        self.assertEqual(client.post("/v1/documents", json={"document": {"description": "x"}}).json["id"], "mongo-id")
        result = client.post("/v1/search", json={"query": "demo", "top_k": 5, "filters": {"status": "active"}})
        self.assertEqual(result.json["results"][0]["skill_name"], "demo")
        self.assertEqual(pushed[0]["description"], "x")

    def test_mcp_storage_uses_http_gateway_not_database_module(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"results": [{"skill_name": "demo"}]}
        with patch.object(mcp_storage.requests, "post", return_value=response) as post:
            result = mcp_storage.retrieve_skill_documents("write", top_k=4)
        self.assertEqual(result[0]["skill_name"], "demo")
        self.assertEqual(post.call_args.args[0].split("/")[-2:], ["v1", "search"])


class SkillRecallTests(unittest.TestCase):
    def test_adaptive_skill_is_searchable_as_verified_cu_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AdaptiveSkillStore(root / "adaptive")
            record = store.save({
                "trace_id": "trace-1", "intent": "Send an email with {{subject}}",
                "description": "Send an email with {{subject}}", "surface": "desktop",
                "variables": {"subject": {"type": "string", "required": True}},
                "steps": [{"action": "type", "intent": "Fill subject", "args": {"text": "{{subject}}"}}],
            }, {"status": "compile_failed"})
            candidate = {
                "engine": "adaptive", "skill_name": record["name"], "version": record["version"],
                "score": .9, "surface": "desktop", "variables": record["variables"],
            }
            result = SkillRecallService(
                LocalSkillRegistry(root / "macros"), adaptive_store=store,
                atlas_searcher=lambda *a, **k: [candidate],
            ).recall("send an email")
            self.assertEqual(result["candidates"][0]["engine"], "adaptive")
            self.assertTrue(result["candidates"][0]["verified"])
            self.assertEqual(result["candidates"][0]["checker"]["type"], "adaptive_cu")

            service = MCPService(
                registry=LocalSkillRegistry(root / "macros"),
                recall_service=SkillRecallService(
                    LocalSkillRegistry(root / "macros"), adaptive_store=store,
                    atlas_searcher=lambda *a, **k: [candidate],
                ),
            )
            response = service.search_skills("send an email")
            self.assertTrue(response["skills"][0]["verified"])
            self.assertEqual(response["skills"][0]["verification_mode"], "adaptive_cu")
            self.assertEqual(response["skills"][0]["checker"]["type"], "adaptive_cu")

    def test_verified_fusion_skill_projects_and_resolves_exact_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = FusionSkillStore(root / "fusion")
            skill = FusedSkill(
                name="fusion_demo", surface="desktop", target="TextEdit",
                params={"text": "secret literal"},
                steps=[Step("Type secret literal", "type", {"text": "secret literal"})],
                verify={"kind": "textedit", "contains": "secret literal"},
            )
            record = store.save_promoted(skill, verified=True)
            document = fusion_skill_document(skill, record)
            self.assertEqual(document["engine"], "fusion")
            self.assertNotIn("secret literal", json.dumps(document))
            recall = SkillRecallService(
                LocalSkillRegistry(root / "macros"), store,
                atlas_searcher=lambda *a, **k: [{**document, "score": .9}],
            ).recall("type a note")
            self.assertEqual(recall["candidates"][0]["engine"], "fusion")
            self.assertEqual(recall["candidates"][0]["version"], record["version"])

    def test_atlas_failure_uses_local_semantic_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "demo.macro.json").write_text(json.dumps(macro()), encoding="utf-8")
            cache = root / "cache.json"
            cache.write_text(json.dumps([{
                "document_key": "skill:macro:demo:v1", "doc_type": "executable_skill",
                "engine": "macro", "skill_name": "demo", "version": 1,
                "surface": "desktop", "status": "active", "checker_verified": True,
                "description": "write text", "variables": {"text": {"type": "string", "required": True}},
                "embedding": [1.0, 0.0],
            }]), encoding="utf-8")
            service = SkillRecallService(
                LocalSkillRegistry(root), atlas_searcher=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")),
                query_embedder=lambda text: [1.0, 0.0], cache_path=cache,
            )
            result = service.recall("make a text file")
            self.assertEqual(result["backend"], "local_cache")
            self.assertEqual(result["candidates"][0]["skill_name"], "demo")
            self.assertIn("RuntimeError", result["atlas_error"])

    def test_close_candidates_are_ambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("first", "second"):
                (root / f"{name}.macro.json").write_text(json.dumps(macro(name=name)), encoding="utf-8")
            atlas = lambda *a, **k: [
                {"engine": "macro", "skill_name": "first", "version": 1, "score": .91},
                {"engine": "macro", "skill_name": "second", "version": 1, "score": .89},
            ]
            result = SkillRecallService(LocalSkillRegistry(root), atlas_searcher=atlas).recall("do it")
            self.assertTrue(result["ambiguous"])
            self.assertFalse(result["auto_executable"])


class ExecuteTaskTests(unittest.TestCase):
    def test_execute_task_binds_extracted_variables_and_replays_macro(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "demo.macro.json").write_text(json.dumps(macro()), encoding="utf-8")
            registry = LocalSkillRegistry(root)
            skill = registry.load_skill("demo")

            class Recall:
                def recall(self, *args, **kwargs):
                    return {
                        "backend": "atlas", "ambiguous": False, "auto_executable": True,
                        "diagnostics": [], "atlas_error": None, "latency_ms": 1,
                        "candidates": [{
                            "engine": "macro", "skill_name": "demo", "version": 1,
                            "score": .9, "variables": {"text": {"type": "string", "required": True}},
                            "executable": skill,
                        }],
                    }

            service = MCPService(
                registry, recall_service=Recall(),
                variable_extractor=lambda intent, defs: {"text": "dynamic value"},
                replayer=lambda skill, params, **kwargs: {
                    "success": params["text"] == "dynamic value", "checker_passed": True,
                    "elapsed_s": 0, "model_calls": 0,
                },
            )
            result = service.execute_task("write dynamic value", confirm_execution=True)["result"]
            self.assertEqual(result["route"], "macro_replay")
            self.assertEqual(result["bound_variables"], ["text"])
            self.assertTrue(result["verified"])


class AutoPromotionTests(unittest.TestCase):
    def test_trace_is_promoted_only_after_clean_checker_verified_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = LocalSkillRegistry(Path(directory) / "skills")
            output = Path(directory) / "result.txt"
            output.write_text("old result", encoding="utf-8")
            compiled = macro(name="learned_task")
            compiled["params"] = {"text": "new result"}
            compiled["checker"] = {
                "type": "text_file", "location": directory,
                "filename": "result.txt", "contains": "{{text}}",
            }
            compiled["steps"] = [{
                "id": "wait", "op": "wait", "seconds": 0,
                "precondition": {}, "postcondition": {}, "timeout": 1,
                "retry_limit": 0, "fallback": [], "why": "test",
            }]
            indexed = []

            def replay(candidate, params, **kwargs):
                self.assertFalse(output.exists())
                output.write_text(params["text"], encoding="utf-8")
                return {"success": True, "checker_passed": True, "elapsed_s": .1, "model_calls": 0}

            result = promote_trace(
                {"intent": "write file", "steps": []}, {"text": "new result"}, registry,
                compiler=lambda trace: compiled, desktop_replayer=replay,
                browser_replayer=lambda *args: {}, indexer=lambda doc: indexed.append(doc) or "atlas-id",
            )
            self.assertEqual(result["status"], "promoted")
            self.assertEqual(registry.load_skill("learned_task")["status"], "active")
            self.assertEqual(indexed[0]["name"], "learned_task")

    def test_trace_without_checker_is_not_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = LocalSkillRegistry(Path(directory) / "skills")
            compiled = macro(name="unchecked")
            compiled["checker"] = {}
            result = promote_trace(
                {"intent": "unsafe task", "steps": []}, {}, registry,
                compiler=lambda trace: compiled, desktop_replayer=lambda *args, **kwargs: {},
                browser_replayer=lambda *args, **kwargs: {}, indexer=lambda doc: "unused",
            )
            self.assertEqual(result["status"], "compile_failed")
            with self.assertRaises(FileNotFoundError):
                registry.load_skill("unchecked")


class TraceMemoryTests(unittest.TestCase):
    def test_projection_redacts_secrets_and_gates_short_or_aborted_runs(self):
        raw = {
            "intent": "  Open   YouTube  ",
            "steps": [
                {"turn": 1, "action": "navigate", "intent": "open", "args": {"url": "https://youtube.com"}},
                {"turn": 2, "action": "type", "intent": "login", "args": {"password": "secret", "text": "query"}},
            ],
            "metrics": {"steps": 2, "elapsed_s": 1, "final": "Done"},
        }
        document = trace_document(raw, "abc")
        self.assertTrue(document["hint_eligible"])
        self.assertFalse(document["verified"])
        self.assertEqual(document["steps"][1]["args"]["password"], "[REDACTED]")
        self.assertEqual(document["intent_hash"], intent_hash("open youtube"))
        raw["steps"] = raw["steps"][:1]
        self.assertFalse(trace_document(raw, "short")["hint_eligible"])
        self.assertFalse(trace_document(raw, "abort", completion_status="aborted")["hint_eligible"])
        raw["steps"] = raw["steps"] * 2
        raw["metrics"]["final"] = ""
        self.assertEqual(trace_document(raw, "empty")["completion_status"], "max_turns_exhausted")
        self.assertFalse(trace_document(raw, "empty")["hint_eligible"])

    def test_trace_projection_replaces_runtime_literals_with_variables(self):
        raw = {
            "intent": 'Write an email with subject "hello"',
            "steps": [{"action": "type", "intent": "Enter hello", "args": {"text": "hello"}},
                    {"action": "key", "intent": "Send", "args": {"key": "return"}}],
            "metrics": {"steps": 2, "final": "Sent hello"},
        }
        document = trace_document(raw, "mail", variables={"email_subject": "hello"})
        encoded = json.dumps(document)
        self.assertNotIn('"hello"', encoded)
        self.assertIn("{{email_subject}}", encoded)
        self.assertEqual(document["variables"], {
            "email_subject": {"type": "string", "required": True},
        })

    def test_hint_excludes_coordinates_and_is_explicitly_unverified(self):
        hint = compact_trace_hint([{
            "trace_id": "t1",
            "steps": [{
                "action": "click", "intent": "Open result",
                "args": {"x": 123, "y": 456, "text": "Video title"},
            }],
        }])
        self.assertIn("unverified", hint.lower())
        self.assertIn("Video title", hint)
        self.assertNotIn('"x"', hint)


@unittest.skipUnless(_HAS_FASTMCP, "fastmcp not installed (optional MCP dependency)")
class FastMCPContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_exposes_expected_tools_in_memory(self):
        from fastmcp import Client
        from rote_mcp.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
        self.assertEqual({tool.name for tool in tools}, {
            "search_skills", "list_skills", "get_skill", "get_skill_history", "replay_skill",
            "execute_new_task", "execute_task", "health",
        })


if __name__ == "__main__":
    unittest.main()
