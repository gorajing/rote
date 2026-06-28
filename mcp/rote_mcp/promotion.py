"""Success-gated MCP promotion of completed Computer Use traces."""
from __future__ import annotations

import copy
import urllib.request
from typing import Callable

from app.desktop_skill_compiler import compile_macro
from app.local_skill_registry import LocalSkillRegistry
from app.macro_skill import resolve_params, validate_macro
from app.verification import location_path

from .descriptors import index_document, queue_pending, skill_document


def _runtime_params(candidate: dict, variables: dict) -> dict:
    params = dict(candidate.get("params", {}))
    params.update({key: value for key, value in variables.items() if key in params})
    return params


def _register_candidate(registry: LocalSkillRegistry, skill: dict) -> dict:
    candidate = copy.deepcopy(skill)
    history = registry.get_history(candidate["name"])
    versions = [int(item["version"]) for item in history]
    candidate.update({
        "version": max(versions, default=0) + 1,
        "parent_version": max(versions) if versions else None,
        "status": "candidate", "candidate_reason": "learned_from_trace",
    })
    validate_macro(candidate)
    registry._atomic_json(registry._version_path(candidate["name"], candidate["version"]), candidate)
    return candidate


def _reset_for_validation(candidate: dict, params: dict) -> None:
    """Create a clean validation state or refuse promotion."""
    if candidate.get("surface", "desktop") == "browser":
        # The browser validator creates a fresh, isolated Playwright context.
        return
    reset = resolve_params(candidate.get("reset", {}), params)
    if reset.get("type") == "http":
        request = urllib.request.Request(reset["url"], method=reset.get("method", "POST"))
        with urllib.request.urlopen(request, timeout=float(reset.get("timeout", 5))):
            return
    checker = resolve_params(candidate.get("checker", {}), params)
    if checker.get("type") in {"file", "text_file", "word_docx"}:
        path = location_path(checker.get("location", params.get("location", "Desktop"))) / checker.get(
            "filename", params.get("filename", "")
        )
        if not path.name:
            raise ValueError("file checker has no filename")
        path.unlink(missing_ok=True)
        return
    raise ValueError("clean reset is unavailable for this desktop task")


def promote_trace(
    trace: dict,
    variables: dict,
    registry: LocalSkillRegistry,
    *,
    desktop_replayer: Callable[..., dict],
    browser_replayer: Callable[..., dict],
    compiler: Callable[[dict], dict] = compile_macro,
    indexer: Callable[[dict], str] = index_document,
) -> dict:
    """Compile, clean-reset, replay, checker-verify, promote, and index one trace."""
    candidate = None
    try:
        compiled = compiler(trace)
        if not compiled.get("checker"):
            raise ValueError("compiler did not produce a deterministic checker")
        candidate = _register_candidate(registry, compiled)
        params = _runtime_params(candidate, variables)
        _reset_for_validation(candidate, params)
        if candidate.get("surface", "desktop") == "browser":
            validation = browser_replayer(candidate, params, registry)
        else:
            validation = desktop_replayer(candidate, params, registry=registry)
        if not validation.get("success") or not validation.get("checker_passed"):
            reason = "; ".join(validation.get("checker_failures", [])) or "replay validation failed"
            registry.reject(candidate, reason)
            return {"status": "rejected", "skill_name": candidate["name"], "reason": reason}
        promoted = registry.promote(candidate, validation)
        document = skill_document(promoted, registry)
        if document is None:
            raise ValueError("promoted skill could not be projected for search")
        try:
            atlas_id = indexer(document)
            index_pending = False
        except Exception as exc:
            queue_pending(document, f"{type(exc).__name__}: {exc}")
            atlas_id = None
            index_pending = True
        return {
            "status": "promoted", "skill_name": promoted["name"],
            "version": promoted["version"], "atlas_id": atlas_id, "index_pending": index_pending,
        }
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        if candidate is not None:
            try:
                registry.reject(candidate, reason)
            except Exception:
                pass
        return {
            "status": "rejected" if candidate is not None else "compile_failed",
            "skill_name": candidate.get("name") if candidate else None,
            "reason": reason,
        }
