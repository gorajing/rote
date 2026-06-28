"""Tool implementations for the Rote chatbot agent.

Four tools are exposed to the agent:
  search_db      — vector-search MongoDB for matching skills
  execute_skill  — replay a skill fetched from MongoDB (scripts always come from the DB)
  computer_use   — drive the real macOS desktop with Gemini CU when no DB match exists
  put_db         — persist a newly compiled skill to MongoDB
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import database.api as db_api
from app import skill_store
from app.macro_skill import validate_macro
from app.desktop_cu import run as cu_run
from app.desktop_skill_compiler import compile_macro
from app.verified_replay import replay_verified


def search_db(description: str) -> list[dict]:
    """Vector-search MongoDB and return the top matching skills.

    Each result is a fully replayable macro (steps + params + checker) plus display metadata
    (name, description, score, variables), so the agent can decide AND pass it straight to
    execute_skill. Skills are stored in the unified top-level schema (see app/skill_store)."""
    raw = db_api.retrieve(description, top_k=3, filters={"doc_type": "skill"})
    results = []
    for r in raw:
        macro = skill_store._doc_to_macro(r)          # steps/params/checker/surface -> replayable
        if macro is None:
            continue
        results.append({
            "_id":         str(r.get("_id", "")),
            "name":        r.get("name", "unknown"),
            "description": r.get("description", r.get("note", "")),
            "site":        r.get("site") or macro.get("app") or macro.get("target", ""),
            "platform":    r.get("platform") or macro.get("surface", ""),
            "variables":   r.get("variables", {}),
            "score":       round(float(r.get("score", 0.0)), 4),
            **macro,
        })
    return results


def execute_skill(skill: dict, params: dict | None = None) -> dict:
    """Replay a skill from MongoDB on the macOS desktop.

    Uses the verified deterministic replay engine — zero model calls for known tasks.
    Runtime `params` override the skill's default param values.
    """
    validate_macro(skill)
    if skill_store._has_call_steps(skill.get("steps", [])):
        raise ValueError("DB replay requires a self-contained skill with no call steps")
    result = replay_verified(skill, params or {})
    return {
        "success":          result.get("success", False),
        "steps_executed":   result.get("steps", 0),
        "elapsed_s":        result.get("elapsed_s", 0),
        "failed_step_id":   result.get("failed_step_id"),
        "checker_passed":   result.get("checker_passed", False),
        "checker_failures": result.get("checker_failures", []),
    }


def computer_use(intent: str) -> dict:
    """Use Gemini Computer Use to perform a task live on the macOS desktop.

    Records the execution trace, compiles it into a reusable keyboard-first macro,
    and returns the compiled skill dict. The caller (agent) must then call put_db.
    """
    fd, trace_path = tempfile.mkstemp(suffix=".json", prefix="rote_trace_")
    os.close(fd)
    try:
        metrics = cu_run(intent, trace_path=trace_path)
        with open(trace_path, encoding="utf-8") as f:
            trace = json.load(f)
        skill = compile_macro(trace)
        # MongoDB embedding requires a 'description' field
        if "description" not in skill:
            skill["description"] = skill.get("note", intent)
        return {
            "skill":   skill,
            "metrics": {k: v for k, v in metrics.items() if k != "trace_path"},
        }
    finally:
        try:
            os.unlink(trace_path)
        except OSError:
            pass


def put_db(skill: dict) -> str:
    """Persist a compiled skill to MongoDB so future search_db calls can find it.

    Routes through the shared store, which flattens any `call` subskills into a self-contained macro
    and writes the unified top-level schema (so the saved skill replays with zero local files).
    Returns the inserted document ID as a string.
    """
    description = skill.get("description") or skill.get("note") or skill.get("name", "")
    validate_macro(skill)
    return skill_store.save_skill(skill, description, skill.get("name"))
