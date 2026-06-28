"""Runtime binding helpers for MCP-recalled fusion skills."""
from __future__ import annotations

import copy

from app.macro_skill import resolve_params
from app.fusion.contract import FusedSkill


def bind_skill(skill: FusedSkill, variables: dict) -> FusedSkill:
    """Return an immutable runtime copy with placeholders resolved everywhere."""
    bound = copy.deepcopy(skill)
    params = {**bound.params, **variables}
    bound.params = resolve_params(params, params)
    bound.target = resolve_params(bound.target, params)
    bound.verify = resolve_params(bound.verify, params)
    for step in bound.steps:
        step.intent = resolve_params(step.intent, params)
        step.args = resolve_params(step.args, params)
    return bound
