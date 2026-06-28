"""In-memory state for AcmeBilling — deterministic, resettable, variant-aware."""
from __future__ import annotations

import copy

VARIANTS = ("baseline", "move_dispute_to_cases", "relabel_export")

DISPUTE_REASONS = (
    "Duplicate charge",
    "Billing error",
    "Contract dispute",
    "Late delivery",
    "Wrong quantity",
)

CUSTOMERS = (
    "Acme Corp", "Globex Inc", "Initech", "Umbrella Co", "Stark Industries",
    "Wayne Enterprises", "Cyberdyne", "Oscorp", "Soylent Corp", "Hooli",
    "Pied Piper", "Massive Dynamic", "Vehement Capital", "Prestige Worldwide",
)


def _build_seed() -> dict:
    invoices = [
        # Hero target — unpaid Acme (hidden until Unpaid filter)
        {"id": "inv-001", "customer": "Acme Corp", "status": "unpaid",
         "amount": 1250.00, "note": "", "exported": False, "flagged": False},
        # Decoy — paid Acme (visible in default Paid filter)
        {"id": "inv-002", "customer": "Acme Corp", "status": "paid",
         "amount": 890.00, "note": "", "exported": False, "flagged": False},
        {"id": "inv-003", "customer": "Initech", "status": "unpaid",
         "amount": 420.00, "note": "", "exported": False, "flagged": False},
        {"id": "inv-004", "customer": "Umbrella Co", "status": "refunded",
         "amount": 2100.00, "note": "overcharge", "exported": True, "flagged": False},
        {"id": "inv-005", "customer": "Stark Industries", "status": "unpaid",
         "amount": 3400.00, "note": "", "exported": False, "flagged": False},
        {"id": "inv-006", "customer": "Globex Inc", "status": "paid",
         "amount": 890.00, "note": "", "exported": False, "flagged": False},
        # Noise — unpaid Acme under $500
        {"id": "inv-007", "customer": "Acme Corp", "status": "unpaid",
         "amount": 320.00, "note": "", "exported": False, "flagged": False},
    ]
    statuses = ["paid", "paid", "refunded", "paid", "paid"]
    for i in range(8, 26):
        cust = CUSTOMERS[i % len(CUSTOMERS)]
        st = statuses[(i - 8) % len(statuses)] if i > 10 else "paid"
        invoices.append({
            "id": f"inv-{i:03d}",
            "customer": cust,
            "status": st,
            "amount": round(200 + (i * 137) % 4800, 2),
            "note": "",
            "exported": st == "refunded",
            "flagged": False,
        })
    return {
        "invoices": invoices,
        "settings": {
            "notifications": True,
            "billing_email": "billing@acme.com",
            "plan": "pro",
        },
    }


SEED: dict = _build_seed()

_store: dict = copy.deepcopy(SEED)
_variant: str = "baseline"
_ui_filter: str = "paid"
_flash: str | None = None


def get_variant() -> str:
    return _variant


def get_ui_filter() -> str:
    return _ui_filter


def get_flash() -> str | None:
    return _flash


def clear_flash() -> None:
    global _flash
    _flash = None


def set_flash(msg: str) -> None:
    global _flash
    _flash = msg


def set_filter(status: str) -> None:
    global _ui_filter
    if status in ("paid", "unpaid", "all"):
        _ui_filter = status


def visible_invoices() -> list[dict]:
    invs = _store["invoices"]
    if _ui_filter == "all":
        return copy.deepcopy(invs)
    if _ui_filter == "unpaid":
        return copy.deepcopy([i for i in invs if i["status"] == "unpaid"])
    return copy.deepcopy([i for i in invs if i["status"] in ("paid", "refunded")])


def snapshot() -> dict:
    return {
        "variant": _variant,
        "ui_filter": _ui_filter,
        "invoices": copy.deepcopy(_store["invoices"]),
        "settings": copy.deepcopy(_store["settings"]),
    }


def reset(variant: str = "baseline") -> dict:
    global _store, _variant, _ui_filter, _flash
    if variant not in VARIANTS:
        variant = "baseline"
    _store = copy.deepcopy(SEED)
    _variant = variant
    _ui_filter = "paid"
    _flash = None
    return snapshot()


def find_invoice(invoice_id: str) -> dict | None:
    for inv in _store["invoices"]:
        if inv["id"] == invoice_id:
            return inv
    return None


def dispute(invoice_id: str, reason: str | None = None) -> bool:
    inv = find_invoice(invoice_id)
    if not inv or not reason or reason not in DISPUTE_REASONS:
        return False
    if inv["status"] not in ("unpaid", "paid"):
        return False
    inv["status"] = "disputed"
    return True


def flag_invoice(invoice_id: str) -> bool:
    inv = find_invoice(invoice_id)
    if not inv:
        return False
    inv["flagged"] = True
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
