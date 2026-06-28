"""FastMCP stdio server exposing Rote skill discovery and replay."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable

from fastmcp import FastMCP

# FastMCP's file inspector may load this module outside the package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from rote_mcp.service import MCPService, ServiceError


mcp = FastMCP(
    "Rote",
    instructions=(
        "Prefer execute_task for natural-language requests. It performs Atlas-first semantic recall, "
        "local semantic fallback, variable binding, verified macro/fusion replay, and uses Computer "
        "Use only on a true miss. Adaptive skills with checker.type=adaptive_cu are verified, eligible "
        "replay candidates and MUST be replayed instead of calling execute_new_task directly. "
        "Use search_skills/get_skill for inspection. Never auto-execute an "
        "ambiguous or lexical-only match. UI-changing tools require confirmation."
    ),
)
service = MCPService()


async def _invoke(function: Callable[..., dict], *args, **kwargs) -> dict:
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except ServiceError as exc:
        print(f"Rote MCP error [{exc.code}]: {exc}", file=sys.stderr)
        return exc.as_dict()


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def search_skills(
    query: str, app: str | None = None, limit: int = 5, surface: str | None = None,
) -> dict:
    """Semantically search Atlas for locally executable desktop skills."""
    return await _invoke(service.search_skills, query, app, limit, surface)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def health() -> dict:
    """Report database-gateway connectivity and local semantic-cache availability."""
    return await _invoke(service.health)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def list_skills(surface: str | None = None) -> dict:
    """List active desktop skills available in the local registry."""
    return await _invoke(service.list_skills, surface)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def get_skill(name: str, version: int | None = None, engine: str = "macro") -> dict:
    """Inspect a local desktop skill without executing it."""
    return await _invoke(service.get_skill, name, version, engine)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def get_skill_history(name: str, engine: str = "macro") -> dict:
    """Return the local version and validation history for a skill."""
    return await _invoke(service.get_skill_history, name, engine)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True})
async def replay_skill(
    name: str,
    version: int,
    params: dict[str, Any] | None = None,
    confirm_execution: bool = False,
    engine: str = "macro",
) -> dict:
    """Replay a pinned desktop skill; this controls the real keyboard and mouse."""
    return await _invoke(service.replay_skill, name, version, params, confirm_execution, engine)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True})
async def execute_new_task(
    intent: str,
    max_turns: int = 18,
    confirm_execution: bool = False,
    variables: dict[str, Any] | None = None,
) -> dict:
    """Use Gemini Computer Use when no verified replay skill matches a new desktop task."""
    return await _invoke(service.execute_new_task, intent, max_turns, confirm_execution, variables)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True})
async def execute_task(
    intent: str,
    variables: dict[str, Any] | None = None,
    app: str | None = None,
    surface: str | None = None,
    confirm_execution: bool = False,
    max_turns: int = 18,
) -> dict:
    """Recall and replay a verified macro/fusion skill, or use CU only on a true semantic miss."""
    return await _invoke(
        service.execute_task, intent, variables, app, surface, confirm_execution, max_turns,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
