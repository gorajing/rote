"""Versioned desktop macro contracts, migration, and parameter resolution."""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
ALLOWED_OPS = {"open_app", "wait", "hotkey", "key", "type", "call"}


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return value or "step"


def _bind_params(value: Any, params: dict) -> Any:
    if isinstance(value, str):
        for name, literal in sorted(params.items(), key=lambda item: len(str(item[1])), reverse=True):
            if literal is not None and str(literal) and str(literal) in value:
                value = value.replace(str(literal), "{{" + name + "}}")
        return value
    if isinstance(value, list):
        return [_bind_params(item, params) for item in value]
    if isinstance(value, dict):
        return {key: _bind_params(item, params) for key, item in value.items()}
    return value


def migrate_macro(macro: dict) -> dict:
    """Return an in-memory v2 copy. The source dictionary/file is never modified."""
    source = copy.deepcopy(macro)
    if source.get("schema_version") == SCHEMA_VERSION:
        validate_macro(source)
        return source

    params = source.get("params", {})
    counters: dict[str, int] = {}
    steps = []
    for raw in source.get("steps", []):
        step = _bind_params(raw, params)
        op = step.get("op", "step")
        counters[op] = counters.get(op, 0) + 1
        step.setdefault("id", f"{_slug(op)}_{counters[op]}")
        step.setdefault("precondition", {})
        step.setdefault("postcondition", {})
        step.setdefault("timeout", float(step.get("seconds", step.get("launch_wait", 3))))
        step.setdefault("retry_limit", 0)
        step.setdefault("fallback", [])
        steps.append(step)

    migrated = {
        **{key: value for key, value in source.items() if key != "steps"},
        "schema_version": SCHEMA_VERSION,
        "version": int(source.get("version", 1)),
        "parent_version": source.get("parent_version"),
        "status": source.get("status", "active"),
        "checker": source.get("checker", {}),
        "stats": source.get("stats", {
            "uses": 0, "successes": 0, "failures": 0,
            "success_rate": 0.0, "avg_duration": 0.0, "model_calls": 0,
        }),
        "steps": steps,
    }
    validate_macro(migrated)
    return migrated


def validate_macro(macro: dict) -> None:
    if macro.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported macro schema: {macro.get('schema_version')}")
    if not macro.get("name") or not isinstance(macro.get("steps"), list):
        raise ValueError("macro requires name and steps")
    seen = set()
    for step in macro["steps"]:
        if step.get("op") not in ALLOWED_OPS:
            raise ValueError(f"unsupported macro op: {step.get('op')}")
        if not step.get("id") or step["id"] in seen:
            raise ValueError(f"missing or duplicate step id: {step.get('id')}")
        if any(key in step for key in ("x", "y", "coords")):
            raise ValueError(f"coordinate-dependent macro step rejected: {step['id']}")
        seen.add(step["id"])


_PARAM = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")


def resolve_params(value: Any, params: dict) -> Any:
    if isinstance(value, str):
        def replace(match):
            name = match.group(1)
            if name not in params:
                raise ValueError(f"missing macro parameter: {name}")
            return str(params[name])
        return _PARAM.sub(replace, value)
    if isinstance(value, list):
        return [resolve_params(item, params) for item in value]
    if isinstance(value, dict):
        return {key: resolve_params(item, params) for key, item in value.items()}
    return value


def default_skill_path(name: str, root: Path) -> Path:
    return root / f"{name}.macro.json"
