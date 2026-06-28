"""FastMCP stdio server exposing Rote desktop skill discovery and replay."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable

from fastmcp import FastMCP

# FastMCP's file inspector loads this module outside the ``app`` package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.mcp_service import MCPService, ServiceError


mcp = FastMCP(
    "Rote",
    instructions=(
        "Search and inspect verified desktop skills before replaying them. "
        "Replay changes the local macOS UI and always requires explicit confirmation."
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
async def search_skills(query: str, app: str | None = None, limit: int = 5) -> dict:
    """Semantically search Atlas for locally executable desktop skills."""
    return await _invoke(service.search_skills, query, app, limit)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def list_skills() -> dict:
    """List active desktop skills available in the local registry."""
    return await _invoke(service.list_skills)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def get_skill(name: str, version: int | None = None) -> dict:
    """Inspect a local desktop skill without executing it."""
    return await _invoke(service.get_skill, name, version)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def get_skill_history(name: str) -> dict:
    """Return the local version and validation history for a skill."""
    return await _invoke(service.get_skill_history, name)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True})
async def replay_skill(
    name: str,
    version: int,
    params: dict[str, Any] | None = None,
    confirm_execution: bool = False,
) -> dict:
    """Replay a pinned desktop skill; this controls the real keyboard and mouse."""
    return await _invoke(service.replay_skill, name, version, params, confirm_execution)


if __name__ == "__main__":
    mcp.run(transport="stdio")
