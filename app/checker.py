"""Deterministic success checker — reads /state, never trusts the model."""
from __future__ import annotations

from typing import Callable

import requests

from .config import APP_URL
from .schemas import Task

CHECKERS: dict[str, Callable[[Task, dict], bool]] = {}


def _register(name: str):
    def decorator(fn: Callable[[Task, dict], bool]):
        CHECKERS[name] = fn
        return fn
    return decorator


def fetch_state() -> dict:
    resp = requests.get(f"{APP_URL}/state", timeout=5)
    resp.raise_for_status()
    return resp.json()


def arena_reset(variant: str | None = "baseline") -> dict:
    url = f"{APP_URL}/reset"
    if variant:
        url += f"?variant={variant}"
    resp = requests.post(url, timeout=5)
    resp.raise_for_status()
    return resp.json()


def check(task: Task) -> bool:
    state = fetch_state()
    checker_fn = CHECKERS.get(task.checker)
    if checker_fn is None:
        raise ValueError(f"Unknown checker: {task.checker}")
    return checker_fn(task, state)


def _matches_row(inv: dict, params: dict) -> bool:
    if params.get("invoice_id") and inv["id"] != params["invoice_id"]:
        return False
    if params.get("customer") and inv["customer"] != params["customer"]:
        return False
    if params.get("min_amount") is not None and inv["amount"] <= params["min_amount"]:
        return False
    if params.get("required_status") and inv["status"] != params["required_status"]:
        return False
    return True


def _find_matching_invoices(state: dict, params: dict) -> list[dict]:
    return [inv for inv in state.get("invoices", []) if _matches_row(inv, params)]


@_register("dispute_workflow")
def _check_dispute_workflow(task: Task, state: dict) -> bool:
    """Match by outcome (note+disputed+exported), not first row for customer."""
    customer = task.params["customer"]
    note = task.params["note"]
    min_amount = task.params.get("min_amount")
    for inv in state.get("invoices", []):
        if inv["customer"] != customer:
            continue
        if min_amount is not None and inv["amount"] <= min_amount:
            continue
        if (inv["status"] == "disputed"
                and inv["note"] == note
                and inv["exported"] is True):
            return True
    return False


@_register("row_find_act")
def _check_row_find_act(task: Task, state: dict) -> bool:
    """Conditional row match: customer + min_amount + status predicates."""
    p = task.params
    candidates = _find_matching_invoices(state, p)
    if not candidates:
        return False
    if p.get("expected_status"):
        return any(inv["status"] == p["expected_status"] for inv in candidates)
    if p.get("require_exported"):
        return any(inv["exported"] for inv in candidates)
    if p.get("expected_note"):
        return any(inv["note"] == p["expected_note"] for inv in candidates)
    return len(candidates) > 0


@_register("settings_change")
def _check_settings_change(task: Task, state: dict) -> bool:
    settings = state.get("settings", {})
    for key, expected in task.params.items():
        if key.startswith("_"):
            continue
        if settings.get(key) != expected:
            return False
    return True
