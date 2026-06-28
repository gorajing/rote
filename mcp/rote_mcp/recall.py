"""Shared Atlas-first semantic recall for MCP skills."""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Callable

from app.local_skill_registry import LocalSkillRegistry
from app.macro_skill import validate_macro

from .descriptors import SEMANTIC_CACHE
from .adaptive_store import AdaptiveSkillStore
from .fusion_adapter import FusionRegistryAdapter
from .storage import embed_query, retrieve_skill_documents
from .variables import variable_definitions


AMBIGUITY_MARGIN = 0.03
SKILL_MATCH_MIN_SCORE = 0.70


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    nl = math.sqrt(sum(a * a for a in left))
    nr = math.sqrt(sum(b * b for b in right))
    return dot / (nl * nr) if nl and nr else 0.0


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower().replace("_", " ")))


class SkillRecallService:
    def __init__(
        self,
        macro_registry: LocalSkillRegistry | None = None,
        fusion_store=None,
        *,
        atlas_searcher: Callable[..., list[dict]] = retrieve_skill_documents,
        query_embedder: Callable[[str], list[float]] = embed_query,
        cache_path: str | Path = SEMANTIC_CACHE,
        adaptive_store: AdaptiveSkillStore | None = None,
    ):
        self.macros = macro_registry or LocalSkillRegistry()
        self.fusion = fusion_store if isinstance(fusion_store, FusionRegistryAdapter) else FusionRegistryAdapter(fusion_store)
        self.atlas_searcher = atlas_searcher
        self.query_embedder = query_embedder
        self.cache_path = Path(cache_path)
        self.adaptive = adaptive_store or AdaptiveSkillStore()

    def _cache(self) -> list[dict]:
        if not self.cache_path.exists():
            return []
        return json.loads(self.cache_path.read_text(encoding="utf-8"))

    def _semantic_cache_search(self, query: str, top_k: int) -> list[dict]:
        cached = self._cache()
        if not cached:
            return []
        vector = self.query_embedder(query)
        items = []
        for document in cached:
            embedding = document.get("embedding")
            if document.get("doc_type") != "executable_skill" or not isinstance(embedding, list):
                continue
            items.append({**document, "score": (1.0 + _cosine(vector, embedding)) / 2.0})
        return sorted(items, key=lambda item: float(item["score"]), reverse=True)[:top_k]

    def _lexical_search(self, query: str, top_k: int) -> list[dict]:
        query_terms = _tokens(query)
        scored = []
        for document in self._cache():
            terms = _tokens(document.get("search_text") or document.get("description", ""))
            score = len(query_terms & terms) / max(1, len(query_terms | terms))
            if score:
                scored.append({**document, "score": score})
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:top_k]

    def _resolve(self, candidate: dict) -> tuple[dict | None, str | None]:
        name = candidate.get("skill_name")
        version = candidate.get("version")
        engine = candidate.get("engine", "macro")
        if not name or version is None:
            return None, "descriptor_missing_identity"
        if engine == "macro":
            try:
                skill = self.macros.load_skill(name, int(version))
                validate_macro(skill)
            except Exception as exc:
                return None, f"macro_load_failed:{type(exc).__name__}"
            verified = bool(
                skill.get("validation", {}).get("success") and
                skill.get("validation", {}).get("checker_passed")
            ) or bool(skill.get("last_run", {}).get("success"))
            if skill.get("status", "active") != "active" or not skill.get("checker") or not verified:
                return None, "macro_not_active_verified"
            if int(skill.get("version", 1)) != int(version):
                return None, "version_mismatch"
            return {
                **candidate, "engine": "macro", "executable": skill,
                "app": candidate.get("app") or skill.get("app"),
                "surface": candidate.get("surface") or skill.get("surface", "desktop"),
                "variables": candidate.get("variables") or variable_definitions(skill.get("params", {})),
                "checker": candidate.get("checker") or skill.get("checker", {}),
                "verified": True, "verification_mode": "deterministic",
                "description": candidate.get("display_description") or candidate.get("description") or skill.get("note", ""),
            }, None
        if engine == "fusion":
            record = self.fusion.load_record(name, int(version))
            skill = self.fusion.load_version(name, int(version))
            if not record or skill is None:
                return None, "fusion_load_failed"
            if record.get("status") != "active" or not record.get("validation", {}).get("verified"):
                return None, "fusion_not_active_verified"
            return {
                **candidate, "engine": "fusion", "executable": skill,
                "app": candidate.get("app") or skill.target,
                "surface": candidate.get("surface") or skill.surface,
                "variables": candidate.get("variables") or variable_definitions(skill.params),
                "checker": candidate.get("checker") or skill.verify,
                "verified": True, "verification_mode": "deterministic",
            }, None
        if engine == "adaptive":
            record = self.adaptive.load_version(name, int(version))
            if not record:
                return None, "adaptive_not_active"
            return {
                **candidate, "engine": "adaptive", "executable": record,
                "app": candidate.get("app") or "Computer Use",
                "surface": candidate.get("surface") or record.get("surface", "desktop"),
                "variables": candidate.get("variables") or record.get("variables", {}),
                "checker": {"type": "adaptive_cu"}, "verified": True,
                "verification_mode": "adaptive_cu",
                "description": candidate.get("display_description") or record["description"],
            }, None
        return None, f"unsupported_engine:{engine}"

    def recall(
        self, query: str, *, limit: int = 5, app: str | None = None,
        surface: str | None = None,
    ) -> dict:
        started = time.perf_counter()
        top_k = min(50, max(20, limit * 4))
        diagnostics = []
        atlas_error = None
        try:
            candidates = self.atlas_searcher(query, top_k=top_k, surface=None)
            backend = "atlas"
        except Exception as exc:
            atlas_error = f"{type(exc).__name__}: {exc}"
            try:
                candidates = self._semantic_cache_search(query, top_k)
                backend = "local_cache"
            except Exception as cache_exc:
                candidates = self._lexical_search(query, top_k)
                backend = "lexical"
                diagnostics.append({"reason": f"embedding_unavailable:{type(cache_exc).__name__}"})

        resolved = []
        for item in candidates:
            if backend != "lexical" and float(item.get("score", 0)) < SKILL_MATCH_MIN_SCORE:
                continue
            value, reason = self._resolve(item)
            if value is None:
                diagnostics.append({
                    "document_key": item.get("document_key"), "skill_name": item.get("skill_name"),
                    "reason": reason,
                })
                continue
            resolved.append(value)

        def rank(item: dict):
            app_penalty = 0 if not app or str(item.get("app", "")).lower() == app.lower() else 1
            surface_penalty = 0 if not surface or item.get("surface") == surface else 1
            return (surface_penalty, app_penalty, -float(item.get("score", 0)))

        resolved.sort(key=rank)
        selected = resolved[:limit]
        ambiguous = bool(
            backend != "lexical" and len(selected) > 1 and
            selected[0].get("skill_name") != selected[1].get("skill_name") and
            abs(float(selected[0].get("score", 0)) - float(selected[1].get("score", 0))) < AMBIGUITY_MARGIN
        )
        return {
            "backend": backend, "candidates": selected, "ambiguous": ambiguous,
            "diagnostics": diagnostics, "atlas_error": atlas_error,
            "retrieved_count": len(candidates), "resolved_count": len(resolved),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "auto_executable": backend != "lexical" and not ambiguous,
        }
