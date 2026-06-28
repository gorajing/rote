"""Application service behind the Rote MCP tools."""
from __future__ import annotations

import json
import contextlib
import sys
import threading
import uuid
import subprocess
from pathlib import Path
from typing import Any, Callable

from app.browser_backend import PlaywrightBrowserBackend
from app.config import MAX_TURNS, TRACES_DIR
from app.local_skill_registry import LocalSkillRegistry
from app.macro_skill import resolve_params, validate_macro
from app.verified_replay import replay_verified

from .promotion import promote_trace
from .recall import SkillRecallService
from .descriptors import adaptive_skill_document, index_document, queue_pending
from .storage import health as storage_health, push_trace, retrieve_skill_documents, retrieve_traces
from .trace_memory import compact_trace_hint, load_trace, trace_document, write_error_trace
from .variables import validate_variables, variable_definitions


def _extract_runtime_variables(intent: str, definitions: dict) -> dict:
    """Extract only declared runtime variables from natural language as structured JSON."""
    if not definitions:
        return {}
    from google import genai
    client = genai.Client()
    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=(
            "Extract values explicitly stated in the user request for the declared variables. "
            "Return one JSON object, omit uncertain values, and never invent a value.\n"
            f"VARIABLES: {json.dumps(definitions)}\nREQUEST: {intent}"
        ),
        config={"response_mime_type": "application/json"},
    )
    value = json.loads(response.text)
    return value if isinstance(value, dict) else {}


def _computer_use(intent: str, *, max_turns: int, trace_path: str, trace_hint: str | None = None) -> dict:
    from app.desktop_cu import run
    return run(intent, max_turns=max_turns, trace_path=trace_path, trace_hint=trace_hint)


def _browser_replay(skill: dict, params: dict, registry: LocalSkillRegistry) -> dict:
    """Verify in isolated Playwright, then leave the verified final URL open in real Chrome."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 720}).new_page()
        start_url = resolve_params(skill.get("start_url", "about:blank"), params)
        page.goto(start_url, wait_until="domcontentloaded")
        result = replay_verified(
            skill, params, backend=PlaywrightBrowserBackend(page), registry=registry,
        )
        final_url, final_title = page.url, page.title()
        browser.close()
    result.update({"surface": "browser", "final_url": final_url, "final_title": final_title})
    if result.get("success") and final_url.startswith(("http://", "https://")):
        subprocess.run(["open", "-a", "Google Chrome", final_url], check=False)
        result["opened_in_chrome"] = True
    else:
        result["opened_in_chrome"] = False
    return result


class ServiceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def as_dict(self) -> dict:
        return {"ok": False, "error": {"code": self.code, "message": str(self)}}


class MCPService:
    def __init__(
        self,
        registry: LocalSkillRegistry | None = None,
        *,
        searcher: Callable[..., list[dict]] = retrieve_skill_documents,
        replayer: Callable[..., dict] = replay_verified,
        computer_user: Callable[..., dict] = _computer_use,
        browser_replayer: Callable[..., dict] = _browser_replay,
        trace_searcher: Callable[..., list[dict]] = retrieve_traces,
        trace_pusher: Callable[[dict], str] = push_trace,
        skill_promoter: Callable[..., dict] = promote_trace,
        recall_service: SkillRecallService | None = None,
        variable_extractor: Callable[[str, dict], dict] = _extract_runtime_variables,
    ):
        self.registry = registry or LocalSkillRegistry()
        self.searcher = searcher
        self.replayer = replayer
        self.computer_user = computer_user
        self.browser_replayer = browser_replayer
        self.trace_searcher = trace_searcher
        self.trace_pusher = trace_pusher
        self.skill_promoter = skill_promoter
        self.recall_service = recall_service or SkillRecallService(
            self.registry, atlas_searcher=searcher,
        )
        self.variable_extractor = variable_extractor
        self._desktop_lock = threading.Lock()

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return json.loads(json.dumps(value, default=str))

    def _load_exact(self, name: str, version: int | None = None) -> dict:
        try:
            skill = self.registry.load_skill(name, version)
        except FileNotFoundError as exc:
            raise ServiceError("skill_not_found", str(exc)) from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise ServiceError("skill_not_found", f"invalid skill {name}: {exc}") from exc
        if version is not None and int(skill.get("version", 1)) != int(version):
            raise ServiceError(
                "version_mismatch",
                f"requested {name} v{version}, local registry resolved v{skill.get('version', 1)}",
            )
        return skill

    @staticmethod
    def _ensure_executable(skill: dict) -> None:
        if skill.get("surface", "desktop") not in {"desktop", "browser"}:
            raise ServiceError("unsupported_surface", "skill surface must be desktop or browser")
        verified = bool(
            skill.get("validation", {}).get("success") and
            skill.get("validation", {}).get("checker_passed")
        ) or bool(skill.get("last_run", {}).get("success"))
        if skill.get("status", "active") != "active" or not skill.get("checker") or not verified:
            raise ServiceError("execution_failed", "skill is not active and checker-verified")
        try:
            validate_macro(skill)
        except ValueError as exc:
            raise ServiceError("execution_failed", f"invalid macro: {exc}") from exc

    def list_skills(self, surface: str | None = None) -> dict:
        if surface not in {None, "desktop", "browser"}:
            raise ServiceError("invalid_params", "surface must be desktop or browser")
        skills = []
        for summary in self.registry.list_skills(surface=surface, status="active"):
            try:
                self._ensure_executable(self._load_exact(summary["name"], summary["version"]))
            except ServiceError:
                continue
            skills.append(summary)
        for skill, record in self.recall_service.fusion.list_active():
            if surface is not None and skill.surface != surface:
                continue
            skills.append({
                "name": skill.name, "version": record["version"], "engine": "fusion",
                "surface": skill.surface, "app": skill.target, "status": "active",
                "params": list(skill.params), "checker": skill.verify,
            })
        for record in self.recall_service.adaptive.list_active():
            if surface is not None and record.get("surface", "desktop") != surface:
                continue
            skills.append({
                "name": record["name"], "version": record["version"], "engine": "adaptive",
                "surface": record.get("surface", "desktop"), "app": "Computer Use",
                "status": "active", "params": list(record.get("variables", {})),
                "checker": {"type": "adaptive_cu"}, "verified": True,
                "verification_mode": "adaptive_cu",
            })
        return {"ok": True, "skills": self._json_safe(skills), "count": len(skills)}

    def health(self) -> dict:
        try:
            database = storage_health()
            return {"ok": True, "database_gateway": database, "local_cache": self.recall_service.cache_path.exists()}
        except Exception as exc:
            return {
                "ok": False, "database_gateway": {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                "local_cache": self.recall_service.cache_path.exists(),
            }

    def get_skill(self, name: str, version: int | None = None, engine: str = "macro") -> dict:
        if engine == "adaptive":
            history = self.recall_service.adaptive.history(name)
            if version is None and history:
                version = max(item["version"] for item in history if item["status"] == "active")
            record = self.recall_service.adaptive.load_version(name, int(version or 0))
            if not record:
                raise ServiceError("skill_not_found", f"adaptive skill not found: {name} v{version}")
            return {"ok": True, "skill": self._json_safe({
                **record, "app": "Computer Use", "checker": {"type": "adaptive_cu"},
                "verified": True, "verification_mode": "adaptive_cu",
            })}
        if engine == "fusion":
            if version is None:
                active = self.recall_service.fusion.load_active(name)
                history = self.recall_service.fusion.history(name)
                if active is None or not history:
                    raise ServiceError("skill_not_found", f"fusion skill not found: {name}")
                version = max(item["version"] for item in history if item.get("status") == "active")
            skill = self.recall_service.fusion.load_version(name, int(version))
            record = self.recall_service.fusion.load_record(name, int(version))
            if skill is None or not record or not record.get("validation", {}).get("verified"):
                raise ServiceError("skill_not_found", f"verified fusion skill not found: {name} v{version}")
            return {"ok": True, "skill": self._json_safe({
                "name": name, "version": version, "engine": "fusion", "surface": skill.surface,
                "app": skill.target, "status": record.get("status"),
                "variables": variable_definitions(skill.params), "checker": skill.verify,
                "steps": [{"op": step.primitive, "why": step.intent} for step in skill.steps],
                "stats": record.get("validation", {}),
            })}
        if engine != "macro":
            raise ServiceError("invalid_params", "engine must be macro, fusion, or adaptive")
        skill = self._load_exact(name, version)
        self._ensure_executable(skill)
        steps = [
            {"id": step["id"], "op": step["op"], "why": step.get("why", "")}
            for step in skill.get("steps", [])
        ]
        return {"ok": True, "skill": self._json_safe({
            "name": skill["name"], "version": skill.get("version", 1),
            "surface": skill.get("surface", "desktop"), "app": skill.get("app"),
            "status": skill.get("status", "active"), "note": skill.get("note", ""),
            "variables": variable_definitions(skill.get("params", {})),
            "checker": skill.get("checker", {}),
            "steps": steps, "stats": skill.get("stats", {}),
        })}

    def get_skill_history(self, name: str, engine: str = "macro") -> dict:
        if engine == "adaptive":
            return {"ok": True, "name": name, "engine": engine,
                    "history": self._json_safe(self.recall_service.adaptive.history(name))}
        if engine == "fusion":
            return {"ok": True, "name": name, "engine": engine,
                    "history": self._json_safe(self.recall_service.fusion.history(name))}
        if engine != "macro":
            raise ServiceError("invalid_params", "engine must be macro, fusion, or adaptive")
        self._load_exact(name)
        history = self.registry.get_history(name)
        return {"ok": True, "name": name, "history": self._json_safe(history)}

    def search_skills(
        self, query: str, app: str | None = None, limit: int = 5, surface: str | None = None,
    ) -> dict:
        if not isinstance(query, str) or not query.strip():
            raise ServiceError("invalid_params", "query must be a non-empty string")
        if limit < 1 or limit > 50:
            raise ServiceError("invalid_params", "limit must be between 1 and 50")
        if surface not in {None, "desktop", "browser"}:
            raise ServiceError("invalid_params", "surface must be desktop or browser")
        recall = self.recall_service.recall(query, limit=limit, app=app, surface=surface)
        print(
            f"Rote recall backend={recall['backend']} latency_ms={recall['latency_ms']} "
            f"retrieved={recall.get('retrieved_count', 0)} resolved={recall.get('resolved_count', 0)} "
            f"rejected={len(recall['diagnostics'])}", file=sys.stderr,
        )
        resolved = [self._json_safe({
            "name": item["skill_name"], "version": item["version"], "engine": item["engine"],
            "score": item.get("score"), "description": item.get("description", ""),
            "app": item.get("app"), "surface": item.get("surface"),
            "variables": item.get("variables", {}), "checker": item.get("checker", {}),
            "verified": bool(item.get("verified")),
            "verification_mode": item.get("verification_mode", "deterministic"),
        }) for item in recall["candidates"]]
        result = {
            "ok": True, "skills": resolved, "count": len(resolved),
            "backend": recall["backend"], "ambiguous": recall["ambiguous"],
            "auto_executable": recall["auto_executable"], "diagnostics": recall["diagnostics"],
            "latency_ms": recall["latency_ms"],
            "retrieved_count": recall.get("retrieved_count", 0),
            "resolved_count": recall.get("resolved_count", 0),
        }
        if recall.get("atlas_error"):
            result["degraded"] = True
            result["atlas_error"] = recall["atlas_error"]
        if recall["ambiguous"]:
            result["next_action"] = "clarify_skill"
            result["hint"] = "multiple skills matched with similar scores; ask the user to clarify"
            return result
        if not resolved:
            result["next_action"] = "execute_new_task" if recall["backend"] != "lexical" else "search_unavailable"
            result["hint"] = ("no verified skill matched; handle as a new task with Gemini Computer Use"
                              if recall["backend"] != "lexical" else
                              "semantic embedding unavailable; lexical matches must not auto-execute")
        return result

    def _replay_fusion(self, skill, variables: dict) -> dict:
        from app.fusion.dispatch import replay
        from app.fusion.verifier import make_verifier
        from .fusion_runtime import bind_skill
        bound = bind_skill(skill, variables)
        if bound.surface == "desktop":
            from app.fusion.desktop_executor import DesktopExecutor
            return replay(bound, DesktopExecutor(), make_verifier(bound), heal=True)
        from playwright.sync_api import sync_playwright
        from app.fusion.browser_executor import BrowserExecutor
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_context(viewport={"width": 1280, "height": 720}).new_page()
            start = bound.target if str(bound.target).startswith(("http://", "https://")) else "about:blank"
            page.goto(start, wait_until="domcontentloaded")
            result = replay(bound, BrowserExecutor(page), make_verifier(bound), heal=True)
            result.update({"final_url": page.url, "final_title": page.title()})
            browser.close()
            return result

    def execute_task(
        self, intent: str, variables: dict | None = None, app: str | None = None,
        surface: str | None = None, confirm_execution: bool = False, max_turns: int = MAX_TURNS,
    ) -> dict:
        if not confirm_execution:
            raise ServiceError("confirmation_required", "task execution requires confirm_execution=true")
        if not isinstance(intent, str) or not intent.strip():
            raise ServiceError("invalid_params", "intent must be a non-empty string")
        supplied = validate_variables(variables)
        recall = self.recall_service.recall(intent, limit=5, app=app, surface=surface)
        if recall["ambiguous"]:
            raise ServiceError("ambiguous_skill", "multiple verified skills matched with similar scores")
        if recall["backend"] == "lexical":
            raise ServiceError("search_unavailable", "semantic embedding unavailable; refusing lexical auto-execution")
        if not recall["candidates"]:
            cold = self.execute_new_task(intent, max_turns, True, supplied)
            cold["result"].update({
                "route": "trace_assisted_cu" if cold["result"].get("used_trace_hint") else "cold_cu",
                "search_backend": recall["backend"], "matched_skill": None,
                "bound_variables": sorted(supplied),
            })
            return cold
        match = recall["candidates"][0]
        definitions = match.get("variables", {})
        unknown = sorted(set(supplied) - set(definitions))
        if unknown:
            raise ServiceError("invalid_params", f"unknown variables: {', '.join(unknown)}")
        bound = dict(supplied)
        missing = sorted(name for name, spec in definitions.items() if spec.get("required") and name not in bound)
        if missing:
            extracted = self.variable_extractor(intent, {name: definitions[name] for name in missing})
            bound.update({name: value for name, value in extracted.items() if name in missing})
            missing = [name for name in missing if name not in bound]
        if missing:
            raise ServiceError("missing_variables", f"missing runtime variables: {', '.join(missing)}")
        if match["engine"] == "macro":
            replayed = self.replay_skill(match["skill_name"], int(match["version"]), bound, True, "macro")
            result = replayed["result"]
            route = "macro_replay"
        elif match["engine"] == "fusion":
            if not self._desktop_lock.acquire(blocking=False):
                raise ServiceError("busy", "another desktop task is already running")
            try:
                result = self._replay_fusion(match["executable"], bound)
            finally:
                self._desktop_lock.release()
            route = "fusion_replay"
        else:
            adaptive = match["executable"]
            hint = compact_trace_hint([adaptive], bound)
            replayed = self.execute_new_task(intent, max_turns, True, bound, hint)
            result = replayed["result"]
            route = "adaptive_cu"
        return {"ok": True, "result": self._json_safe({
            **result, "route": route, "search_backend": recall["backend"],
            "matched_skill": {
                "name": match["skill_name"], "version": match["version"],
                "engine": match["engine"], "score": match.get("score"),
            },
            "bound_variables": sorted(bound), "verified": bool(result.get("success") or result.get("verified")),
            "promotion": None,
        })}

    def replay_skill(
        self,
        name: str,
        version: int,
        params: dict | None = None,
        confirm_execution: bool = False,
        engine: str = "macro",
    ) -> dict:
        if not confirm_execution:
            raise ServiceError("confirmation_required", "desktop replay requires confirm_execution=true")
        if engine == "fusion":
            skill = self.recall_service.fusion.load_version(name, version)
            record = self.recall_service.fusion.load_record(name, version)
            if skill is None or not record or record.get("status") != "active" or not record.get("validation", {}).get("verified"):
                raise ServiceError("skill_not_found", f"verified fusion skill not found: {name} v{version}")
            overrides = validate_variables(params)
            unknown, missing = sorted(set(overrides) - set(skill.params)), sorted(set(skill.params) - set(overrides))
            if unknown or missing:
                details = ([f"unknown: {', '.join(unknown)}"] if unknown else []) + ([f"missing: {', '.join(missing)}"] if missing else [])
                raise ServiceError("invalid_params", "; ".join(details))
            if not self._desktop_lock.acquire(blocking=False):
                raise ServiceError("busy", "another desktop task is already running")
            try:
                return {"ok": True, "result": self._json_safe(self._replay_fusion(skill, overrides))}
            finally:
                self._desktop_lock.release()
        if engine == "adaptive":
            record = self.recall_service.adaptive.load_version(name, version)
            if not record:
                raise ServiceError("skill_not_found", f"adaptive skill not found: {name} v{version}")
            overrides = validate_variables(params)
            missing = sorted(set(record.get("variables", {})) - set(overrides))
            if missing:
                raise ServiceError("invalid_params", f"missing: {', '.join(missing)}")
            hint = compact_trace_hint([record], overrides)
            return self.execute_new_task(record["intent"], MAX_TURNS, True, overrides, hint)
        if engine != "macro":
            raise ServiceError("invalid_params", "engine must be macro, fusion, or adaptive")
        skill = self._load_exact(name, version)
        self._ensure_executable(skill)
        overrides = params or {}
        if not isinstance(overrides, dict):
            raise ServiceError("invalid_params", "params must be an object")
        defaults = skill.get("params", {})
        unknown = sorted(set(overrides) - set(defaults))
        if unknown:
            raise ServiceError("invalid_params", f"unknown parameters: {', '.join(unknown)}")
        missing = sorted(set(defaults) - set(overrides))
        if missing:
            raise ServiceError("invalid_params", f"missing runtime variables: {', '.join(missing)}")
        merged = dict(overrides)
        try:
            resolve_params(skill, merged)
        except ValueError as exc:
            raise ServiceError("invalid_params", str(exc)) from exc
        if not self._desktop_lock.acquire(blocking=False):
            raise ServiceError("busy", "another desktop replay is already running")
        try:
            try:
                if skill.get("surface", "desktop") == "browser":
                    result = self.browser_replayer(skill, merged, self.registry)
                else:
                    result = self.replayer(skill, merged, registry=self.registry)
            except Exception as exc:
                raise ServiceError("execution_failed", f"desktop replay failed: {exc}") from exc
            self.registry.record_run(skill, result)
            return {"ok": True, "result": self._json_safe(result)}
        finally:
            self._desktop_lock.release()

    def execute_new_task(
        self,
        intent: str,
        max_turns: int = MAX_TURNS,
        confirm_execution: bool = False,
        variables: dict | None = None,
        trace_hint_override: str | None = None,
    ) -> dict:
        """Use Gemini Computer Use for a task that has no matching verified skill."""
        if not confirm_execution:
            raise ServiceError("confirmation_required", "Computer Use requires confirm_execution=true")
        if not isinstance(intent, str) or not intent.strip():
            raise ServiceError("invalid_params", "intent must be a non-empty string")
        if max_turns < 1 or max_turns > 40:
            raise ServiceError("invalid_params", "max_turns must be between 1 and 40")
        try:
            variables = validate_variables(variables)
        except ValueError as exc:
            raise ServiceError("invalid_params", str(exc)) from exc
        if not self._desktop_lock.acquire(blocking=False):
            raise ServiceError("busy", "another desktop task is already running")
        trace_id = uuid.uuid4().hex
        trace_path = str(Path(TRACES_DIR) / "mcp" / f"cold-{trace_id}.json")
        source_traces = []
        trace_hint = None
        try:
            if trace_hint_override:
                trace_hint = trace_hint_override
            else:
                try:
                    source_traces = self.trace_searcher(intent.strip(), surface="desktop", top_k=3)
                    trace_hint = compact_trace_hint(source_traces, variables)
                except Exception:
                    # Trace memory is an optimization. Gateway/index outages must not block cold CU.
                    source_traces = []
                    trace_hint = None
            try:
                # FastMCP owns stdout for protocol messages. CU progress belongs on stderr.
                with contextlib.redirect_stdout(sys.stderr):
                    metrics = self.computer_user(
                        intent.strip(), max_turns=max_turns, trace_path=trace_path,
                        trace_hint=trace_hint,
                    )
            except Exception as exc:
                raw_trace = write_error_trace(trace_path, intent.strip(), str(exc))
                document = trace_document(
                    raw_trace, trace_id, completion_status="error", variables=variables,
                )
                persisted = False
                try:
                    self.trace_pusher(document)
                    persisted = True
                except Exception:
                    pass
                raise ServiceError(
                    "execution_failed",
                    f"Computer Use failed: {exc}; trace_id={trace_id}; trace_persisted={persisted}",
                ) from exc
            final = str(metrics.get("final", ""))
            reason = metrics.get("termination_reason")
            if reason == "model_completed" and final.strip():
                completion_status = "model_completed"
            elif reason == "max_turns_exhausted" or not final.strip():
                completion_status = "max_turns_exhausted"
            else:
                completion_status = "aborted"
            document = trace_document(
                load_trace(trace_path), trace_id,
                completion_status=completion_status, variables=variables,
            )
            trace_persisted = False
            trace_persist_error = None
            try:
                self.trace_pusher(document)
                trace_persisted = True
            except Exception as exc:
                trace_persist_error = f"{type(exc).__name__}: {exc}"
            promotion = {"status": "not_attempted", "reason": "CU did not complete successfully"}
            if completion_status == "model_completed" and document.get("hint_eligible"):
                try:
                    promotion = self.skill_promoter(
                        load_trace(trace_path), variables, self.registry,
                        desktop_replayer=self.replayer,
                        browser_replayer=self.browser_replayer,
                    )
                except Exception as exc:
                    promotion = {"status": "compile_failed", "reason": f"{type(exc).__name__}: {exc}"}
                if promotion.get("status") != "promoted":
                    adaptive = self.recall_service.adaptive.save(document, promotion)
                    descriptor = adaptive_skill_document(adaptive)
                    try:
                        adaptive_id = index_document(descriptor)
                        adaptive_pending = False
                    except Exception as exc:
                        queue_pending(descriptor, f"{type(exc).__name__}: {exc}")
                        adaptive_id = None
                        adaptive_pending = True
                    promotion = {
                        **promotion, "adaptive_skill": adaptive["name"],
                        "adaptive_version": adaptive["version"], "adaptive_id": adaptive_id,
                        "adaptive_pending": adaptive_pending,
                    }
            result = {
                **metrics,
                "mode": "computer_use",
                "used_skill": False,
                "verified": False,
                "completion_status": completion_status,
                "trace_id": trace_id,
                "trace_persisted": trace_persisted,
                "trace_persist_error": trace_persist_error,
                "used_trace_hint": bool(trace_hint),
                "source_trace_ids": [
                    item.get("trace_id") or str(item.get("_id")) for item in source_traces
                ],
                "promotion": promotion,
                "verified": bool(
                    promotion.get("status") == "promoted" or promotion.get("adaptive_skill")
                ),
                "note": (
                    "The trace was compiled, clean-replayed, checker-verified, and promoted."
                    if promotion.get("status") == "promoted" else
                    "Stored as a reusable adaptive CU skill because deterministic promotion did not pass."
                ),
            }
            return {"ok": True, "result": self._json_safe(result)}
        finally:
            self._desktop_lock.release()
