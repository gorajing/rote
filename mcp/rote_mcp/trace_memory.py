"""Projection and safe MCP hint rendering for unverified Computer Use traces."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.macro_skill import resolve_params
from .variables import parameterize, validate_variables, variable_definitions


_SECRET = re.compile(r"password|passwd|token|secret|api[_-]?key|authorization", re.I)
_COORDINATES = {"x", "y", "coords", "destination_x", "destination_y"}
_HINT_ARGS = {"url", "app", "name", "browser", "keys", "key", "query", "text", "direction"}


def normalize_intent(intent: str) -> str:
    return " ".join(intent.lower().split())


def intent_hash(intent: str) -> str:
    return hashlib.sha256(normalize_intent(intent).encode("utf-8")).hexdigest()


def _redact(value: Any, key: str = "") -> Any:
    if _SECRET.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + "…"
    return value


def load_trace(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def trace_document(
    trace: dict,
    trace_id: str,
    *,
    surface: str = "desktop",
    completion_status: str | None = None,
    variables: dict | None = None,
) -> dict:
    variables = validate_variables(variables)
    intent = str(parameterize(trace.get("intent", ""), variables)).strip()
    steps = parameterize(_redact(trace.get("steps", [])), variables)
    metrics = _redact(trace.get("metrics", {}))
    final = str(parameterize(metrics.get("final", trace.get("final", "")), variables))
    status = completion_status or (
        "aborted" if final.startswith("ABORTED:") else
        "model_completed" if final.strip() else
        "max_turns_exhausted"
    )
    eligible = bool(intent) and status == "model_completed" and len(steps) >= 2
    return {
        "document_key": f"trace:{trace_id}",
        "doc_type": "execution_trace",
        "trace_id": trace_id,
        "intent": intent,
        "description": intent,
        "intent_hash": intent_hash(intent),
        "surface": surface,
        "completion_status": status,
        "verified": False,
        "hint_eligible": eligible,
        "variables": variable_definitions(variables),
        "steps": steps,
        "metrics": {key: metrics[key] for key in (
            "steps", "elapsed_s", "tokens", "model_s", "screenshot_s", "execute_s",
        ) if key in metrics},
        "final": final,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def write_error_trace(path: str | Path, intent: str, error: str) -> dict:
    value = {
        "intent": intent,
        "steps": [],
        "metrics": {"steps": 0, "final": f"ERROR: {error}"},
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, indent=2), encoding="utf-8")
    return value


def compact_trace_hint(traces: list[dict], runtime_variables: dict | None = None) -> str | None:
    runtime_variables = validate_variables(runtime_variables)
    blocks = []
    for trace in traces:
        lines = []
        for step in trace.get("steps", []):
            action = str(step.get("action", "action"))
            semantic = str(resolve_params(step.get("intent", ""), runtime_variables)).strip()
            args = {}
            for key, value in step.get("args", {}).items():
                if key in _HINT_ARGS and key not in _COORDINATES and not _SECRET.search(key):
                    resolved = resolve_params(value, runtime_variables)
                    args[key] = resolved[:500] if isinstance(resolved, str) else resolved
            detail = semantic or action.replace("_", " ")
            suffix = f" {json.dumps(args, ensure_ascii=False)}" if args else ""
            lines.append(f"- {detail} [{action}]{suffix}")
        if lines:
            blocks.append(f"Past run {trace.get('trace_id', trace.get('_id', 'unknown'))}:\n" + "\n".join(lines))
    if not blocks:
        return None
    return (
        "These are unverified past execution traces for similar goals. Treat them only as hints; "
        "re-ground every action against the current screen and ignore stale or unsafe steps.\n\n"
        + "\n\n".join(blocks)
    )
