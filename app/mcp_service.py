"""Application service behind the Rote MCP tools."""
from __future__ import annotations

import json
import threading
from typing import Any, Callable

from database.api import retrieve

from .local_skill_registry import LocalSkillRegistry
from .macro_skill import resolve_params, validate_macro
from .verified_replay import replay_verified


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
        searcher: Callable[..., list[dict]] = retrieve,
        replayer: Callable[..., dict] = replay_verified,
    ):
        self.registry = registry or LocalSkillRegistry()
        self.searcher = searcher
        self.replayer = replayer
        self._replay_lock = threading.Lock()

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
        if skill.get("surface", "desktop") != "desktop":
            raise ServiceError("unsupported_surface", "v1 only supports desktop skills")
        if skill.get("status", "active") != "active" or not skill.get("checker"):
            raise ServiceError("execution_failed", "skill is not active and checker-verified")
        try:
            validate_macro(skill)
        except ValueError as exc:
            raise ServiceError("execution_failed", f"invalid macro: {exc}") from exc

    def list_skills(self) -> dict:
        skills = self.registry.list_skills(surface="desktop", status="active")
        return {"ok": True, "skills": self._json_safe(skills), "count": len(skills)}

    def get_skill(self, name: str, version: int | None = None) -> dict:
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
            "params": skill.get("params", {}), "checker": skill.get("checker", {}),
            "steps": steps, "stats": skill.get("stats", {}),
        })}

    def get_skill_history(self, name: str) -> dict:
        self._load_exact(name)
        history = self.registry.get_history(name)
        return {"ok": True, "name": name, "history": self._json_safe(history)}

    def search_skills(self, query: str, app: str | None = None, limit: int = 5) -> dict:
        if not isinstance(query, str) or not query.strip():
            raise ServiceError("invalid_params", "query must be a non-empty string")
        if limit < 1 or limit > 50:
            raise ServiceError("invalid_params", "limit must be between 1 and 50")
        filters = {
            "doc_type": "executable_skill", "surface": "desktop", "status": "active",
            "checker_verified": True,
        }
        if app:
            filters["app"] = app
        try:
            matches = self.searcher(query, top_k=limit, filters=filters)
        except Exception as exc:
            raise ServiceError("search_unavailable", f"skill search failed: {exc}") from exc

        resolved = []
        for match in matches:
            name, version = match.get("skill_name"), match.get("version")
            if not name or version is None:
                continue
            try:
                skill = self._load_exact(name, int(version))
                self._ensure_executable(skill)
            except ServiceError:
                continue
            resolved.append(self._json_safe({
                "name": name, "version": int(version), "score": match.get("score"),
                "description": match.get("description", skill.get("note", "")),
                "app": skill.get("app"), "params": skill.get("params", {}),
                "checker": skill.get("checker", {}),
            }))
        return {"ok": True, "skills": resolved, "count": len(resolved)}

    def replay_skill(
        self,
        name: str,
        version: int,
        params: dict | None = None,
        confirm_execution: bool = False,
    ) -> dict:
        if not confirm_execution:
            raise ServiceError("confirmation_required", "desktop replay requires confirm_execution=true")
        skill = self._load_exact(name, version)
        self._ensure_executable(skill)
        overrides = params or {}
        if not isinstance(overrides, dict):
            raise ServiceError("invalid_params", "params must be an object")
        defaults = skill.get("params", {})
        unknown = sorted(set(overrides) - set(defaults))
        if unknown:
            raise ServiceError("invalid_params", f"unknown parameters: {', '.join(unknown)}")
        merged = {**defaults, **overrides}
        try:
            resolve_params(skill, merged)
        except ValueError as exc:
            raise ServiceError("invalid_params", str(exc)) from exc
        if not self._replay_lock.acquire(blocking=False):
            raise ServiceError("busy", "another desktop replay is already running")
        try:
            try:
                result = self.replayer(skill, merged, registry=self.registry)
            except Exception as exc:
                raise ServiceError("execution_failed", f"desktop replay failed: {exc}") from exc
            self.registry.record_run(skill, result)
            return {"ok": True, "result": self._json_safe(result)}
        finally:
            self._replay_lock.release()
