"""MongoDB-backed skill store — the voice agent's ONLY source of skills (no local files at runtime).

Search and push both hit the Atlas `tasks` collection via database/api.py (semantic vector search on
the `description` field, whose index is READY). A learned skill is stored as a self-contained,
replayable document; retrieval hands the macro straight back to the replay engine.

  search(text)            -> a replayable macro for the best semantic match, or None
  save_skill(macro, desc) -> push a learned skill to `tasks`  (this is the `push_database` op)
  list_skills()           -> [{name, description}] for the agent catalog + STT keyterms

CLI:
  python -m app.skill_store --list                 # what's in the DB
  python -m app.skill_store --seed                 # one-time: push local demo macros into `tasks`
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from database import api
from .macro_skill import migrate_macro

REPO = Path(__file__).resolve().parent.parent
LOCAL_SKILLS = REPO / "database" / "skills"

# Atlas vectorSearchScore below which a "hit" isn't really a match -> the agent should learn instead.
MATCH_THRESHOLD = float(os.getenv("ROTE_SKILL_MATCH_THRESHOLD", "0.82"))


def _doc_to_macro(doc: dict) -> dict | None:
    """Coerce a `tasks` document into a replayable macro, or None if it isn't one.

    Skills we push carry the full macro under `macro`. Documents written by other tooling may keep
    macro-format steps at the top level; we wrap those into a minimal macro. Anything else (e.g. a
    raw browser trace in a different step format) returns None so the agent learns it fresh."""
    macro = doc.get("macro")
    if isinstance(macro, dict) and isinstance(macro.get("steps"), list):
        return macro
    steps = doc.get("steps")
    if isinstance(steps, list) and steps and all(isinstance(s, dict) and "op" in s for s in steps):
        return {
            "schema_version": 2,
            "name": doc.get("name") or doc.get("intent_hash") or "task",
            "surface": doc.get("surface", "desktop"),
            "params": doc.get("variables") if isinstance(doc.get("variables"), dict) else {},
            "checker": doc.get("checker", {}),
            "steps": steps,
        }
    return None


def search(text: str, threshold: float | None = None) -> dict | None:
    """Semantic search `tasks`; return a replayable macro for the best match above threshold."""
    threshold = MATCH_THRESHOLD if threshold is None else threshold
    try:
        hits = api.retrieve(text, top_k=3)
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

    Stores a self-contained, replayable document whose `description` is vector-indexed for search."""
    macro = migrate_macro(macro)
    name = name or macro.get("name") or "task"
    doc = {
        "doc_type": "skill",
        "name": name,
        "description": description,
        "surface": macro.get("surface", "desktop"),
        "macro": macro,                               # the replayable skill (read back by search())
        "steps": macro.get("steps", []),              # mirror at top level for schema consistency
        "variables": macro.get("params", {}),
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
    """One-time migration: push the local demo macros into `tasks` so the agent finds them in the DB.
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


def main() -> None:
    if "--seed" in sys.argv:
        ids = seed_from_local()
        print(f"\nseeded {len(ids)} skill(s) into the `tasks` collection.")
    else:
        for skill in list_skills():
            print(f"{skill['name']:24} :: {skill['description'][:64]}")


if __name__ == "__main__":
    main()
