"""Task bank with train/heldout splits — transfer-friendly design."""
from __future__ import annotations

from .schemas import Task

HERO = Task(
    id="hero-dispute",
    site="billing",
    intent="Find the unpaid invoice from Acme Corp, mark it disputed, "
           "add the note 'duplicate charge', then export the receipt.",
    params={"customer": "Acme Corp", "note": "duplicate charge"},
    checker="dispute_workflow",
    family="invoice_action",
)

TRAIN: list[Task] = [
    HERO,
    Task(
        id="train-dispute-initech",
        site="billing",
        intent="Find the unpaid invoice from Initech, mark it disputed, "
               "add the note 'billing error', then export the receipt.",
        params={"customer": "Initech", "note": "billing error"},
        checker="dispute_workflow",
        family="invoice_action",
    ),
    Task(
        id="train-dispute-stark",
        site="billing",
        intent="Find the unpaid invoice from Stark Industries, mark it disputed, "
               "add the note 'contract dispute', then export the receipt.",
        params={"customer": "Stark Industries", "note": "contract dispute"},
        checker="dispute_workflow",
        family="invoice_action",
    ),
    Task(
        id="train-refund-globex",
        site="billing",
        intent="Find the paid invoice from Globex Inc and refund it.",
        params={"customer": "Globex Inc", "expected_status": "refunded"},
        checker="row_find_act",
        family="row_find_act",
    ),
    Task(
        id="train-settings-plan",
        site="billing",
        intent="Go to Settings and change the plan to enterprise.",
        params={"plan": "enterprise"},
        checker="settings_change",
        family="settings_change",
    ),
]

HELDOUT: list[Task] = [
    Task(
        id="held-dispute-stark",
        site="billing",
        intent="Find the unpaid invoice from Stark Industries, mark it disputed, "
               "add the note 'late delivery', then export the receipt.",
        params={"customer": "Stark Industries", "note": "late delivery"},
        checker="dispute_workflow",
        family="invoice_action",
    ),
    Task(
        id="held-dispute-initech",
        site="billing",
        intent="Find the unpaid invoice from Initech, mark it disputed, "
               "add the note 'wrong quantity', then export the receipt.",
        params={"customer": "Initech", "note": "wrong quantity"},
        checker="dispute_workflow",
        family="invoice_action",
    ),
    Task(
        id="held-refund-globex",
        site="billing",
        intent="Find the paid invoice from Globex Inc and refund it.",
        params={"customer": "Globex Inc", "expected_status": "refunded"},
        checker="row_find_act",
        family="row_find_act",
    ),
    Task(
        id="held-settings-email",
        site="billing",
        intent="Go to Settings and change the billing email to finance@acme.com.",
        params={"billing_email": "finance@acme.com"},
        checker="settings_change",
        family="settings_change",
    ),
    Task(
        id="held-settings-notifications",
        site="billing",
        intent="Go to Settings and turn off email notifications.",
        params={"notifications": False},
        checker="settings_change",
        family="settings_change",
    ),
]

SPLITS: dict[str, list[Task]] = {
    "train": TRAIN,
    "heldout": HELDOUT,
    "all": TRAIN + HELDOUT,
}
