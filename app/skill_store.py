"""MongoDB-backed replayable skill store for the voice agent and chat TUI.

Search and push both hit the Atlas `tasks` collection via database/api.py (semantic vector search on
the `description` field, whose index is READY). A learned skill is stored as a self-contained,
replayable document; retrieval hands the macro straight back to the replay engine.

  search(text)            -> a replayable macro for the best semantic match, or None
  save_skill(macro, desc) -> push a learned skill to `tasks`  (this is the `push_database` op)
  list_skills()           -> [{name, description}] for the agent catalog + STT keyterms

CLI:
  python -m app.skill_store --list                 # what's in the DB
  python -m app.skill_store --seed                 # push any local runtime macros, if present
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from database import api
from .local_skill_registry import LocalSkillRegistry
from .macro_skill import migrate_macro, resolve_params

REPO = Path(__file__).resolve().parent.parent
LOCAL_SKILLS = REPO / "database" / "skills"

# Atlas vectorSearchScore below which a "hit" isn't really a match -> the agent should learn instead.
MATCH_THRESHOLD = float(os.getenv("ROTE_SKILL_MATCH_THRESHOLD", "0.82"))


def flatten_macro(macro: dict, registry=None) -> dict:
    """Inline every `call` subskill into one self-contained list of primitive steps, so the stored
    macro replays from MongoDB with NO local-file lookups. Top-level parameter placeholders
    (e.g. {{calculation}}, {{filename}}) are preserved so the skill stays parameterizable; only the
    subskill *structure* is expanded (its params are bound to whatever the call passed)."""
    registry = registry or LocalSkillRegistry()
    macro = migrate_macro(macro)

    def inline(steps: list, mapping: dict | None, prefix: str) -> list:
        out = []
        for step in steps:
            if step.get("op") != "call":
                # top level (mapping is None): keep placeholders. Inside a subskill: bind its params.
                item = copy.deepcopy(step) if mapping is None else resolve_params(step, mapping)
                if prefix:                              # keep step ids unique across inlined subskills
                    item["id"] = f"{prefix}__{item.get('id', 'step')}"
                out.append(item)
            else:
                child = migrate_macro(registry.load_skill(step["skill"], step.get("version")))
                passed = step.get("params", {})
                passed = dict(passed) if mapping is None else resolve_params(passed, mapping)
                child_map = {**child.get("params", {}), **passed}
                child_prefix = f"{prefix}__{step['id']}" if prefix else step["id"]
                out.extend(inline(child["steps"], child_map, child_prefix))
        return out

    return {**{k: v for k, v in macro.items() if k != "steps"}, "steps": inline(macro["steps"], None, "")}


# Bookkeeping/metadata fields that live on a `tasks` document but are not part of the macro itself.
_NON_MACRO_KEYS = {"_id", "score", "embedding", "doc_type", "description",
                   "verified", "created_at", "site", "platform"}

_PLACEHOLDER = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")


def _placeholders(obj) -> set:
    """Every {{name}} referenced anywhere inside a macro (steps + checker)."""
    if isinstance(obj, str):
        return set(_PLACEHOLDER.findall(obj))
    if isinstance(obj, list):
        return set().union(*(_placeholders(x) for x in obj)) if obj else set()
    if isinstance(obj, dict):
        return set().union(*(_placeholders(v) for v in obj.values())) if obj else set()
    return set()


def _has_call_steps(steps: list) -> bool:
    return any(isinstance(step, dict) and step.get("op") == "call" for step in steps)


def _doc_to_macro(doc: dict) -> dict | None:
    """Coerce a `tasks` document into a replayable macro, or None if it isn't one.

    The unified schema stores the macro AT THE TOP LEVEL (steps, params, checker, surface, ...), so a
    skill document IS a macro plus a few bookkeeping fields. We strip the bookkeeping and return the
    rest. Foreign documents (e.g. execution traces whose steps aren't macro ops) return None so the
    agent learns the task fresh instead of trying to replay something unrunnable."""
    steps = doc.get("steps")
    if not (isinstance(steps, list) and steps and all(isinstance(s, dict) and "op" in s for s in steps)):
        return None
    if _has_call_steps(steps):
        return None
    if doc.get("status", "active") != "active":
        return None
    macro = {k: v for k, v in doc.items() if k not in _NON_MACRO_KEYS}
    macro.setdefault("schema_version", 2)
    macro.setdefault("name", doc.get("name") or "task")
    macro.setdefault("surface", doc.get("surface", "desktop"))
    macro.setdefault("params", {})
    if not macro.get("checker"):
        return None
    # Only treat as replayable if every {{placeholder}} has a default value in params. This rejects
    # broken docs (e.g. older 'variables'-only skills with no param values) so the agent never tries
    # to replay something whose placeholders it can't fill -> it cleanly learns the task instead.
    referenced = _placeholders(macro.get("steps", [])) | _placeholders(macro.get("checker", {}))
    if referenced - set(macro["params"]):            # a placeholder with no default value
        return None
    return macro


def search(text: str, threshold: float | None = None) -> dict | None:
    """Semantic search `tasks`; return a replayable macro for the best match above threshold."""
    threshold = MATCH_THRESHOLD if threshold is None else threshold
    try:
        hits = api.retrieve(text, top_k=3, filters={"doc_type": "skill"})
    except Exception:
        return None
    for doc in hits:
        if float(doc.get("score", 0.0)) < threshold:
            break                                     # hits are score-ordered; nothing better follows
        macro = _doc_to_macro(doc)
        if macro is not None:
            return macro
    return None


def save_skill(macro: dict, description: str, name: str | None = None) -> str:
    """Push a learned skill to the `tasks` collection. This is the `push_database` operation.

    Stores a self-contained, replayable document whose `description` is vector-indexed for search.
    The macro is flattened first so it has NO `call` steps -> replay needs no local files. Stored in
    the unified top-level schema: the macro's own fields (steps, params, checker, variables, surface,
    ...) sit at the document root, alongside `description` (vector-indexed) and `doc_type`."""
    macro = flatten_macro(macro)
    name = name or macro.get("name") or "task"
    doc = {
        **{k: v for k, v in macro.items() if k not in _NON_MACRO_KEYS},
        "name": name,
        "description": description,
        "doc_type": "skill",
        "verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return api.push(doc)


def list_skills(limit: int = 200) -> list[dict]:
    """All stored skills as {name, description, surface} for the agent catalog + STT keyterms."""
    out: list[dict] = []
    try:
        docs = api.list_all(limit)
    except Exception:
        return out
    for d in docs:
        desc = d.get("description") or d.get("intent")
        name = d.get("name") or d.get("intent_hash")
        if desc and name and _doc_to_macro(d) is not None:   # only list replayable skills
            out.append({"name": str(name), "description": str(desc),
                        "surface": d.get("surface", "desktop")})
    return out


def seed_from_local() -> list[str]:
    """Push local runtime macros into `tasks` when a developer has recreated/promoted them locally.
    Idempotent — skips any skill whose name is already stored. Not used at runtime (runtime is DB-only)."""
    existing = {s["name"] for s in list_skills()}
    ids = []
    for path in sorted(LOCAL_SKILLS.glob("*.macro.json")):
        if path.name.startswith("stale_"):
            continue
        macro = json.loads(path.read_text(encoding="utf-8"))
        if macro.get("surface", "desktop") != "desktop" or not macro.get("checker"):
            continue                                  # skip browser skills + subskill building blocks
        name = macro.get("name") or path.stem
        if name in existing:
            print(f"skip {path.name}  (already in DB as '{name}')")
            continue
        description = macro.get("note") or macro.get("description") or name
        skill_id = save_skill(macro, description, name)
        ids.append(skill_id)
        print(f"seeded {path.name}  ->  {skill_id}  ({description[:60]})")
    return ids


def clear_skills() -> int:
    """Delete only our own skill documents (doc_type='skill') from `tasks`. Leaves other docs
    (e.g. execution traces written by other tooling) untouched."""
    return api._collection().delete_many({"doc_type": "skill"}).deleted_count


def main() -> None:
    if "--reseed" in sys.argv:
        removed = clear_skills()
        print(f"removed {removed} existing skill doc(s)")
        ids = seed_from_local()
        print(f"\nre-seeded {len(ids)} flattened skill(s) into the `tasks` collection.")
    elif "--seed" in sys.argv:
        ids = seed_from_local()
        print(f"\nseeded {len(ids)} skill(s) into the `tasks` collection.")
    else:
        for skill in list_skills():
            print(f"{skill['name']:24} :: {skill['description'][:64]}")


if __name__ == "__main__":
    main()
