"""Project executable local skills into MCP search documents."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.local_skill_registry import LocalSkillRegistry
from app.macro_skill import resolve_params

from .fusion_adapter import FusionRegistryAdapter
from .adaptive_store import AdaptiveSkillStore
from .storage import prepare_document, push_document
from .variables import parameterize, variable_definitions


_SHORTCUT_FIELDS = {
    "open_app": ("app", "launch_wait"), "key": ("key",), "hotkey": ("keys",),
    "type": ("text",), "wait": ("seconds",),
}
SEMANTIC_CACHE = Path(__file__).resolve().parents[2] / "database" / "skills" / "registry" / "semantic_cache.json"
PENDING_SYNC = SEMANTIC_CACHE.with_name("pending_skill_sync.json")


def _search_text(name: str, app: str, description: str, variables: dict, surface: str) -> str:
    words = name.replace("_", " ")
    variable_words = " ".join(key.replace("_", " ") for key in variables)
    return " | ".join(item for item in (words, description, app, surface, variable_words) if item)


def _expand_shortcut_steps(
    skill: dict, params: dict, registry: LocalSkillRegistry | None, seen: set[str] | None = None,
) -> list[dict]:
    seen = set(seen or ())
    if skill["name"] in seen:
        raise ValueError(f"recursive skill call: {skill['name']}")
    seen.add(skill["name"])
    result = []
    for raw in parameterize(skill.get("steps", []), params):
        if raw.get("op") == "call":
            if registry is None:
                raise ValueError("registry is required to project composed shortcuts")
            child = registry.load_skill(raw["skill"], raw.get("version"))
            child_params = dict(child.get("params", {}))
            child_params.update(resolve_params(raw.get("params", {}), params))
            result.extend(_expand_shortcut_steps(child, child_params, registry, seen))
            continue
        op = raw.get("op")
        if op not in _SHORTCUT_FIELDS:
            raise ValueError(f"desktop shortcut cannot project op: {op}")
        resolved = resolve_params(raw, params)
        step = {"op": op, "why": str(raw.get("why", op))}
        for field in _SHORTCUT_FIELDS[op]:
            if field in resolved:
                step[field] = resolved[field]
        result.append(step)
    return result


def skill_document(skill: dict, registry: LocalSkillRegistry | None = None) -> dict | None:
    """Build an Atlas descriptor, excluding skills that are unsafe to execute."""
    surface = skill.get("surface", "desktop")
    if surface not in {"desktop", "browser"}:
        return None
    verified = bool(
        skill.get("validation", {}).get("success") and
        skill.get("validation", {}).get("checker_passed")
    ) or bool(skill.get("last_run", {}).get("success"))
    if skill.get("status", "active") != "active" or not skill.get("checker") or not verified:
        return None
    name = skill["name"]
    if name.startswith("stale_"):
        return None
    version = int(skill.get("version", 1))
    app = skill.get("app") or "macOS"
    note = skill.get("note") or f"Run the verified {name.replace('_', ' ')} workflow in {app}."
    stored_note = parameterize(note, skill.get("params", {}))
    variables = variable_definitions(skill.get("params", {}))
    common = {
        "document_key": f"skill:macro:{name}:v{version}", "doc_type": "executable_skill",
        "engine": "macro", "skill_name": name, "version": version, "surface": surface,
        "status": "active", "checker_verified": True, "app": app,
        "display_description": stored_note,
        "search_text": _search_text(name, app, stored_note, variables, surface),
        "variables": variables, "source_ref": f"macro:{name}:v{version}",
    }
    common["description"] = common["search_text"]
    if surface == "desktop":
        symbolic = {key: "{{" + key + "}}" for key in skill.get("params", {})}
        parameterized = {**skill, "steps": parameterize(skill.get("steps", []), skill.get("params", {}))}
        return {
            **common,
            "name": name,
            "os": "macos",
            "steps": _expand_shortcut_steps(parameterized, symbolic, registry),
            "checker": parameterize(skill.get("checker", {}), skill.get("params", {})),
        }
    return {
        **common,
        "steps": parameterize(skill.get("steps", []), skill.get("params", {})),
        "checker": parameterize(skill.get("checker", {}), skill.get("params", {})),
    }


def fusion_skill_document(skill, record: dict) -> dict:
    """Project a checker-verified fusion skill without persisting runtime literals or crops."""
    name, version = skill.name, int(record["version"])
    params = dict(skill.params)
    variables = variable_definitions(params)
    intents = [parameterize(step.intent, params) for step in skill.steps]
    description = "; ".join(intents) or f"Run the verified {name.replace('_', ' ')} workflow."
    app = skill.target or skill.surface
    return {
        "document_key": f"skill:fusion:{name}:v{version}", "doc_type": "executable_skill",
        "engine": "fusion", "skill_name": name, "version": version,
        "surface": skill.surface, "app": app, "status": "active", "checker_verified": True,
        "display_description": description,
        "search_text": _search_text(name, app, description, variables, skill.surface),
        "variables": variables, "source_ref": f"fusion:{name}:v{version}",
        "checker": parameterize(skill.verify, params),
        "steps": [{"intent": intent, "primitive": step.primitive} for intent, step in zip(intents, skill.steps)],
    } | {"description": _search_text(name, app, description, variables, skill.surface)}


def adaptive_skill_document(record: dict) -> dict:
    description = record["description"]
    return {
        "document_key": f"skill:adaptive:{record['name']}:v{record['version']}",
        "doc_type": "executable_skill", "engine": "adaptive",
        "skill_name": record["name"], "version": record["version"],
        "surface": record.get("surface", "desktop"), "app": "Computer Use",
        "status": "active", "checker_verified": True, "verified": True,
        "verification_mode": "adaptive_cu", "checker": {"type": "adaptive_cu"},
        "description": description, "display_description": description,
        "search_text": description, "variables": record.get("variables", {}),
        "source_ref": f"adaptive:{record['name']}:v{record['version']}",
    }


def build_documents(
    registry: LocalSkillRegistry | None = None, fusion_store=None,
    adaptive_store: AdaptiveSkillStore | None = None,
) -> list[dict]:
    registry = registry or LocalSkillRegistry()
    documents = []
    for summary in registry.list_skills():
        skill = registry.load_skill(summary["name"], summary["version"])
        document = skill_document(skill, registry)
        if document is not None:
            documents.append(document)
    if fusion_store is None:
        fusion_store = FusionRegistryAdapter()
    elif not hasattr(fusion_store, "list_active"):
        fusion_store = FusionRegistryAdapter(fusion_store)
    documents.extend(fusion_skill_document(skill, record) for skill, record in fusion_store.list_active())
    documents.extend(adaptive_skill_document(record) for record in (adaptive_store or AdaptiveSkillStore()).list_active())
    return documents


def _write_cache(documents: list[dict]) -> None:
    SEMANTIC_CACHE.parent.mkdir(parents=True, exist_ok=True)
    temporary = SEMANTIC_CACHE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(documents, indent=2), encoding="utf-8")
    os.replace(temporary, SEMANTIC_CACHE)


def queue_pending(document: dict, error: str) -> None:
    entries = json.loads(PENDING_SYNC.read_text(encoding="utf-8")) if PENDING_SYNC.exists() else []
    entries = [item for item in entries if item.get("document", {}).get("document_key") != document.get("document_key")]
    entries.append({"document": document, "error": error})
    temporary = PENDING_SYNC.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    os.replace(temporary, PENDING_SYNC)


def index_document(document: dict) -> str:
    prepared = prepare_document(document)
    current = []
    if SEMANTIC_CACHE.exists():
        current = json.loads(SEMANTIC_CACHE.read_text(encoding="utf-8"))
    current = [item for item in current if item.get("document_key") != prepared["document_key"]]
    current.append(prepared)
    _write_cache(current)
    return push_document(prepared)


def sync_index(
    registry: LocalSkillRegistry | None = None, fusion_store=None,
    adaptive_store: AdaptiveSkillStore | None = None,
) -> list[str]:
    documents = [
        prepare_document(item)
        for item in build_documents(registry, fusion_store, adaptive_store)
    ]
    _write_cache(documents)
    ids = [push_document(document) for document in documents]
    if PENDING_SYNC.exists():
        PENDING_SYNC.unlink()
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Index active Rote desktop macros in Atlas")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    documents = build_documents()
    if args.dry_run:
        print(json.dumps(documents, indent=2))
        return
    print(json.dumps({"indexed": len(sync_index()), "documents": len(documents)}))


if __name__ == "__main__":
    main()
