"""In-memory state for AcmeBilling — deterministic, resettable, variant-aware."""
from __future__ import annotations

import copy

VARIANTS = ("baseline", "move_actions", "rename_export", "reorder_table", "relabel_nav")

SEED: dict = {
    "invoices": [
        {"id": "inv-001", "customer": "Acme Corp", "status": "unpaid",
         "amount": 1250.00, "note": "", "exported": False},
        {"id": "inv-002", "customer": "Globex Inc", "status": "paid",
         "amount": 890.00, "note": "", "exported": False},
        {"id": "inv-003", "customer": "Initech", "status": "unpaid",
         "amount": 420.00, "note": "", "exported": False},
        {"id": "inv-004", "customer": "Umbrella Co", "status": "refunded",
         "amount": 2100.00, "note": "overcharge", "exported": True},
        {"id": "inv-005", "customer": "Stark Industries", "status": "unpaid",
         "amount": 3400.00, "note": "", "exported": False},
    ],
    "settings": {
        "notifications": True,
        "billing_email": "billing@acme.com",
        "plan": "pro",
    },
}

_store: dict = copy.deepcopy(SEED)
_variant: str = "baseline"
_flash: str | None = None


def get_variant() -> str:
    return _variant


def get_flash() -> str | None:
    return _flash


def clear_flash() -> None:
    global _flash
    _flash = None


def set_flash(msg: str) -> None:
    global _flash
    _flash = msg


def snapshot() -> dict:
    return {"variant": _variant, "invoices": copy.deepcopy(_store["invoices"]),
            "settings": copy.deepcopy(_store["settings"])}


def reset(variant: str = "baseline") -> dict:
    global _store, _variant, _flash
    if variant not in VARIANTS:
        variant = "baseline"
    _store = copy.deepcopy(SEED)
    _variant = variant
    _flash = None
    return snapshot()


def find_invoice(invoice_id: str) -> dict | None:
    for inv in _store["invoices"]:
        if inv["id"] == invoice_id:
            return inv
    return None


def find_invoice_by_customer(customer: str) -> dict | None:
    for inv in _store["invoices"]:
        if inv["customer"] == customer:
            return inv
    return None


def dispute(invoice_id: str) -> bool:
    inv = find_invoice(invoice_id)
    if not inv or inv["status"] not in ("unpaid", "paid"):
        return False
    inv["status"] = "disputed"
    return True


def set_note(invoice_id: str, note: str) -> bool:
    inv = find_invoice(invoice_id)
    if not inv:
        return False
    inv["note"] = note
    return True


def export_receipt(invoice_id: str) -> bool:
    inv = find_invoice(invoice_id)
    if not inv:
        return False
    inv["exported"] = True
    return True


def refund(invoice_id: str) -> bool:
    inv = find_invoice(invoice_id)
    if not inv or inv["status"] not in ("unpaid", "paid", "disputed"):
        return False
    inv["status"] = "refunded"
    return True


def update_settings(**kwargs) -> bool:
    for key, val in kwargs.items():
        if key in _store["settings"]:
            _store["settings"][key] = val
    return True
