"""Rote — FROZEN CONTRACTS. All four workstreams code against these. Freeze at H0;
do not change a field without telling everyone. The Trajectory is the central object —
observation, eval, judging, and improvement are all functions of it."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Task:
    id: str
    site: str                       # "billing"
    intent: str                     # natural-language goal the CU model receives
    params: dict                    # {"customer": "Acme Corp", "note": "duplicate charge"}
    checker: str                    # key in checker.CHECKERS, e.g. "dispute_workflow"
    family: str                     # "invoice_action" | "row_find_act" | "settings_change"


@dataclass
class Step:
    turn: int
    intent: str                     # Gemini-3.5 per-step reasoning (the keystone field)
    action: str                     # "click" | "type" | "scroll" | "navigate" | "press_key" | ...
    args: dict                      # raw model args (normalized coords, text, key, ...)
    coords: tuple | None            # denormalized (x, y) actually executed (None if non-spatial)
    screenshot_path: str            # pre-action screenshot
    url: str


@dataclass
class Trajectory:
    task_id: str
    steps: list[Step] = field(default_factory=list)
    final_text: str | None = None
    success: bool | None = None     # filled by the deterministic checker — NEVER by the model
    used_skill: str | None = None   # name of the injected skill, if any

    @property
    def n_steps(self) -> int:
        return len(self.steps)


@dataclass
class Skill:
    name: str                       # "dispute_unpaid_invoice"
    site: str
    goal_template: str              # "Mark the {status} invoice from {customer} as {action}, add note, export"
    variables: dict                 # {"customer": {"type": "string", "required": True}, ...}
    preconditions: dict             # {"url_pattern": "/billing", "landmarks": ["invoice table"]}
    steps: list[dict]               # [{"intent":.., "action":.., "target_desc":"Invoices nav item", "param_ref":"customer"}]
    success_checks: list[str]       # human-readable; the REAL check is Task.checker
    embedding: list[float] = field(default_factory=list)
    stats: dict = field(default_factory=lambda: {
        "uses": 0, "successes": 0, "failures": 0, "success_rate": 0.0, "avg_steps": 0.0})
    version: int = 1
    status: str = "active"          # "active" | "deprecated"
