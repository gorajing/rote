"""MCP variable contracts and literal-to-placeholder projection."""
from __future__ import annotations

import re
from typing import Any


_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def validate_variables(values: dict | None) -> dict:
    values = values or {}
    if not isinstance(values, dict):
        raise ValueError("variables must be an object")
    for name in values:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError(f"invalid variable name: {name!r}")
    return values


def variable_definitions(values: dict) -> dict:
    """Describe runtime inputs without persisting their literal values."""
    result = {}
    for name, value in values.items():
        if isinstance(value, bool):
            kind = "boolean"
        elif isinstance(value, int):
            kind = "integer"
        elif isinstance(value, float):
            kind = "number"
        elif isinstance(value, list):
            kind = "array"
        elif isinstance(value, dict):
            kind = "object"
        else:
            kind = "string"
        result[name] = {"type": kind, "required": True}
    return result


def parameterize(value: Any, variables: dict) -> Any:
    """Replace every supplied runtime literal with its named placeholder recursively."""
    if isinstance(value, str):
        output = value
        literals = sorted(
            ((name, str(literal)) for name, literal in variables.items()
             if literal is not None and len(str(literal)) >= 2),
            key=lambda item: len(item[1]), reverse=True,
        )
        for name, literal in literals:
            output = output.replace(literal, "{{" + name + "}}")
        return output
    if isinstance(value, list):
        return [parameterize(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: parameterize(item, variables) for key, item in value.items()}
    return value
