"""Task bank with train/heldout splits — transfer-friendly design."""
from __future__ import annotations

from .schemas import Task

_SANDBOX = (
    "This is a local QA billing app you own at localhost. No real money or accounts. "
)

HERO_INTENT = (
    _SANDBOX
    + "Apply the Unpaid status filter, find the unpaid Acme Corp invoice for $1,250, "
    "open the row menu (⋯), choose Mark Disputed and select 'Duplicate charge' in the "
    "reason modal, add note 'duplicate charge', then export the receipt."
)

HERO = Task(
    id="hero-dispute",
    site="billing",
    intent=HERO_INTENT,
    params={"customer": "Acme Corp", "note": "duplicate charge", "min_amount": 500},
    checker="dispute_workflow",
    family="invoice_action",
)

TRAIN: list[Task] = [
    HERO,
    Task(
        id="train-dispute-initech",
        site="billing",
        intent=_SANDBOX + (
            "Apply the Unpaid filter, find the unpaid Initech invoice, open the row menu, "
            "Mark Disputed with reason 'Billing error', add note 'billing error', export receipt."
        ),
        params={"customer": "Initech", "note": "billing error"},
        checker="dispute_workflow",
        family="invoice_action",
    ),
    Task(
        id="train-dispute-stark",
        site="billing",
        intent=_SANDBOX + (
            "Apply the Unpaid filter, find the unpaid Stark Industries invoice, open the row menu, "
            "Mark Disputed with reason 'Contract dispute', add note 'contract dispute', export receipt."
        ),
        params={"customer": "Stark Industries", "note": "contract dispute"},
        checker="dispute_workflow",
        family="invoice_action",
    ),
    Task(
        id="train-refund-globex",
        site="billing",
        intent=_SANDBOX + "Find the paid Globex Inc invoice and refund it via the row menu.",
        params={"customer": "Globex Inc", "expected_status": "refunded"},
        checker="row_find_act",
        family="row_find_act",
    ),
    Task(
        id="train-row-acme",
        site="billing",
        intent=_SANDBOX + (
            "Apply the Unpaid filter, scroll to find the unpaid Acme Corp invoice over $500, "
            "and refund it via the row menu."
        ),
        params={"customer": "Acme Corp", "min_amount": 500, "expected_status": "refunded"},
        checker="row_find_act",
        family="row_find_act",
    ),
    Task(
        id="train-settings-plan",
        site="billing",
        intent=_SANDBOX + "Go to Settings and change the plan to enterprise.",
        params={"plan": "enterprise"},
        checker="settings_change",
        family="settings_change",
    ),
]

HELDOUT: list[Task] = [
    Task(
        id="held-dispute-stark",
        site="billing",
        intent=_SANDBOX + (
            "Apply the Unpaid filter, find the unpaid Stark Industries invoice, open the row menu, "
            "Mark Disputed with reason 'Late delivery', add note 'late delivery', export receipt."
        ),
        params={"customer": "Stark Industries", "note": "late delivery"},
        checker="dispute_workflow",
        family="invoice_action",
    ),
    Task(
        id="held-dispute-initech",
        site="billing",
        intent=_SANDBOX + (
            "Apply the Unpaid filter, find the unpaid Initech invoice, open the row menu, "
            "Mark Disputed with reason 'Wrong quantity', add note 'wrong quantity', export receipt."
        ),
        params={"customer": "Initech", "note": "wrong quantity"},
        checker="dispute_workflow",
        family="invoice_action",
    ),
    Task(
        id="held-refund-globex",
        site="billing",
        intent=_SANDBOX + "Find the paid Globex Inc invoice and refund it via the row menu.",
        params={"customer": "Globex Inc", "expected_status": "refunded"},
        checker="row_find_act",
        family="row_find_act",
    ),
    Task(
        id="held-settings-email",
        site="billing",
        intent=_SANDBOX + "Go to Settings and change the billing email to finance@acme.com.",
        params={"billing_email": "finance@acme.com"},
        checker="settings_change",
        family="settings_change",
    ),
    Task(
        id="held-settings-notifications",
        site="billing",
        intent=_SANDBOX + "Go to Settings and turn off email notifications.",
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
