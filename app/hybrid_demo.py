"""Hybrid Mac app + browser demo using the shared verified replay path."""
from __future__ import annotations

import argparse
import copy
import json
import re
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from .browser_backend import PlaywrightBrowserBackend
from .browser_self_improve import _reset
from .local_skill_registry import LocalSkillRegistry
from .verified_replay import MacOSDesktopBackend, replay_verified


LOCAL_APP_URL = "http://localhost:8800"


def _rewrite_urls(value: Any, app_url: str) -> Any:
    if isinstance(value, str):
        return value.replace(LOCAL_APP_URL, app_url.rstrip("/"))
    if isinstance(value, list):
        return [_rewrite_urls(item, app_url) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_urls(item, app_url) for key, item in value.items()}
    return value


def prepare_browser_skill(skill: dict, app_url: str) -> dict:
    """Return a browser skill copy pointed at the requested AcmeBilling base URL."""
    return _rewrite_urls(copy.deepcopy(skill), app_url.rstrip("/"))


def _plain_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _parse_decimal(value: str) -> Decimal:
    cleaned = re.sub(r"[^0-9.+-]", "", value)
    if cleaned in {"", ".", "+", "-"}:
        raise ValueError(f"no decimal value found in clipboard: {value!r}")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal value in clipboard: {value!r}") from exc


def _currency(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def credit_note(invoice_id: str, calculator_clipboard: str) -> str:
    credit = _parse_decimal(calculator_clipboard)
    return f"Credit calculated in Calculator for {invoice_id}: {_currency(credit)}"


def _request_json(url: str, *, method: str = "GET") -> dict:
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def reset_acme(app_url: str, *, variant: str = "baseline") -> dict:
    return _request_json(f"{app_url.rstrip('/')}/reset?variant={variant}", method="POST")


def fetch_invoice(app_url: str, invoice_id: str) -> tuple[int, dict]:
    state = _request_json(f"{app_url.rstrip('/')}/state")
    for index, invoice in enumerate(state.get("invoices", [])):
        if invoice.get("id") == invoice_id:
            return index, invoice
    raise ValueError(f"invoice not found: {invoice_id}")


def build_calculator_credit_skill(amount: Decimal, credit_rate: Decimal) -> tuple[dict, str]:
    calculation = f"{_plain_decimal(amount)}*{_plain_decimal(credit_rate)}"
    expected = _plain_decimal(amount * credit_rate)
    skill = {
        "schema_version": 2,
        "surface": "desktop",
        "name": "calculate_invoice_credit",
        "app": "Calculator",
        "os": "macos",
        "version": 1,
        "parent_version": None,
        "status": "active",
        "params": {"calculation": calculation, "expected_result": expected},
        "checker": {"type": "condition", "condition": {"clipboard_contains": "{{expected_result}}"}},
        "stats": {},
        "steps": [
            {"id": "open_calculator", "op": "open_app", "app": "Calculator", "launch_wait": 6,
             "precondition": {}, "postcondition": {"foreground_app": "Calculator"},
             "timeout": 6, "retry_limit": 1, "fallback": [], "why": "Open Calculator"},
            {"id": "clear_calculator", "op": "key", "key": "escape",
             "precondition": {"foreground_app": "Calculator"},
             "postcondition": {"foreground_app": "Calculator"},
             "timeout": 2, "retry_limit": 0, "fallback": [], "why": "Clear the previous calculation"},
            {"id": "calculate_credit", "op": "type", "text": "{{calculation}}=",
             "precondition": {"foreground_app": "Calculator"},
             "postcondition": {"foreground_app": "Calculator"},
             "timeout": 2, "retry_limit": 0, "fallback": [], "why": "Calculate the invoice credit"},
            {"id": "copy_credit", "op": "hotkey", "keys": ["command", "c"],
             "precondition": {"foreground_app": "Calculator"},
             "postcondition": {"clipboard_contains": "{{expected_result}}"},
             "timeout": 2, "retry_limit": 1, "fallback": [], "why": "Copy the calculated credit"},
        ],
    }
    return skill, calculation


def build_invoice_note_skill(app_url: str, invoice_id: str, invoice_index: int, note: str) -> dict:
    base = app_url.rstrip("/")
    return {
        "schema_version": 2,
        "surface": "browser",
        "name": "acme_invoice_credit_note",
        "app": "AcmeBilling",
        "os": "any",
        "version": 1,
        "parent_version": None,
        "status": "active",
        "start_url": f"{base}/invoice/{invoice_id}/note",
        "params": {"invoice_id": invoice_id, "note": note},
        "checker": {
            "type": "http_json",
            "url": f"{base}/state",
            "equals": {f"invoices.{invoice_index}.note": "{{note}}"},
        },
        "stats": {},
        "steps": [
            {"id": "open_invoice_note", "op": "navigate",
             "url": f"{base}/invoice/{invoice_id}/note",
             "precondition": {},
             "postcondition": {"url_contains": f"/invoice/{invoice_id}/note", "text_contains": "Add Note"},
             "timeout": 8, "retry_limit": 1, "fallback": [], "why": "Open the invoice note form"},
            {"id": "enter_credit_note", "op": "fill", "target": {"label": "Invoice Note"},
             "text": "{{note}}", "precondition": {"element_visible": "Invoice Note"},
             "postcondition": {"element_visible": "Invoice Note"},
             "timeout": 5, "retry_limit": 0, "fallback": [], "why": "Enter the Calculator-derived credit note"},
            {"id": "save_credit_note", "op": "click", "target": {"role": "button", "name": "Save Note"},
             "precondition": {"element_visible": "Save Note"},
             "postcondition": {"url_contains": "/billing", "text_contains": "Note saved"},
             "timeout": 5, "retry_limit": 0, "fallback": [], "why": "Save the invoice note"},
        ],
    }


def run_desktop_app(app_name: str, *, backend=None, launch_wait: float = 6.0) -> dict:
    """Open a native macOS app and verify the desktop surface observed it running."""
    backend = backend or MacOSDesktopBackend()
    execution = backend.execute({
        "op": "open_app",
        "app": app_name,
        "launch_wait": launch_wait,
        "precondition": {},
        "postcondition": {"app_running": app_name},
        "timeout": launch_wait,
        "retry_limit": 0,
        "fallback": [],
    })
    state = backend.inspect()
    running_apps = state.get("running_apps", [])
    foreground_app = state.get("foreground_app")
    running = app_name in running_apps or foreground_app == app_name
    return {
        "ok": running,
        "app": app_name,
        "foreground_app": foreground_app,
        "running": running,
        "focused": foreground_app == app_name,
        "execution": execution,
    }


def _browser_result_summary(result: dict) -> dict:
    keys = (
        "success", "checker_passed", "checker_failures", "failed_step_id",
        "steps", "elapsed_s", "model_calls", "repair_calls", "mode",
        "skill_name", "skill_version",
    )
    return {key: result.get(key) for key in keys if key in result}


def run_hybrid_demo(
    *,
    app_url: str = LOCAL_APP_URL,
    billing_email: str = "hybrid@acme.example",
    mac_app: str = "Calculator",
    headless: bool = False,
    keep_open: float = 0.0,
) -> dict:
    """Run one native-app action, then one verified browser workflow."""
    registry = LocalSkillRegistry()
    skill = prepare_browser_skill(registry.load_skill("acme_settings_email"), app_url)
    params = {**skill.get("params", {}), "billing_email": billing_email}
    desktop = run_desktop_app(mac_app)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_context(viewport={"width": 1280, "height": 720}).new_page()
        _reset(skill, params, page)
        result = replay_verified(
            skill,
            params,
            allow_repair=False,
            backend=PlaywrightBrowserBackend(page),
            registry=registry,
        )
        if keep_open > 0:
            page.wait_for_timeout(keep_open * 1000)
        browser.close()

    browser_summary = _browser_result_summary(result)
    return {
        "ok": bool(desktop.get("ok") and browser_summary.get("success")),
        "desktop": desktop,
        "browser": browser_summary,
        "app_url": app_url.rstrip("/"),
        "billing_email": billing_email,
    }


def run_invoice_credit_note_demo(
    *,
    app_url: str = LOCAL_APP_URL,
    invoice_id: str = "inv-001",
    credit_rate: str = "0.10",
    variant: str = "baseline",
    headless: bool = False,
    keep_open: float = 0.0,
) -> dict:
    """Use Calculator to compute an invoice credit, then write it into AcmeBilling."""
    app_url = app_url.rstrip("/")
    reset_acme(app_url, variant=variant)
    invoice_index, invoice = fetch_invoice(app_url, invoice_id)
    amount = Decimal(str(invoice["amount"]))
    rate = Decimal(str(credit_rate))
    desktop_skill, calculation = build_calculator_credit_skill(amount, rate)
    desktop_backend = MacOSDesktopBackend()
    desktop_result = replay_verified(desktop_skill, backend=desktop_backend, allow_repair=False)
    desktop_state = desktop_backend.inspect()
    clipboard = desktop_state.get("clipboard", "")
    if not desktop_result.get("success"):
        return {
            "ok": False,
            "workflow": "invoice-credit-note",
            "invoice_id": invoice_id,
            "calculation": calculation,
            "calculator_clipboard": clipboard,
            "desktop": _browser_result_summary(desktop_result),
            "browser": None,
            "error": "calculator credit skill failed verification",
        }

    note = credit_note(invoice_id, clipboard)
    browser_skill = build_invoice_note_skill(app_url, invoice_id, invoice_index, note)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_context(viewport={"width": 1280, "height": 720}).new_page()
        browser_result = replay_verified(
            browser_skill,
            allow_repair=False,
            backend=PlaywrightBrowserBackend(page),
            registry=LocalSkillRegistry(),
        )
        if keep_open > 0:
            page.wait_for_timeout(keep_open * 1000)
        browser.close()

    browser_summary = _browser_result_summary(browser_result)
    desktop_summary = _browser_result_summary(desktop_result)
    return {
        "ok": bool(desktop_result.get("success") and browser_summary.get("success")),
        "workflow": "invoice-credit-note",
        "app_url": app_url,
        "variant": variant,
        "invoice_id": invoice_id,
        "invoice_amount": float(amount),
        "credit_rate": str(rate),
        "calculation": calculation,
        "calculator_clipboard": clipboard,
        "note": note,
        "desktop": desktop_summary,
        "browser": browser_summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a hybrid Mac app + browser Rote demo")
    parser.add_argument("--workflow", choices=("app-email", "invoice-credit-note"),
                        default="app-email", help="hybrid workflow to run")
    parser.add_argument("--app-url", default=LOCAL_APP_URL, help="AcmeBilling base URL")
    parser.add_argument("--email", default="hybrid@acme.example", help="billing email to set")
    parser.add_argument("--mac-app", default="Calculator", help="native macOS app to open")
    parser.add_argument("--invoice-id", default="inv-001", help="invoice used by invoice-credit-note")
    parser.add_argument("--credit-rate", default="0.10", help="credit multiplier for invoice-credit-note")
    parser.add_argument("--variant", default="baseline", help="AcmeBilling UI variant")
    parser.add_argument("--headless", action="store_true", help="run browser headlessly")
    parser.add_argument("--keep-open", type=float, default=0.0,
                        help="seconds to keep the browser open after the run")
    args = parser.parse_args()
    if args.workflow == "invoice-credit-note":
        result = run_invoice_credit_note_demo(
            app_url=args.app_url,
            invoice_id=args.invoice_id,
            credit_rate=args.credit_rate,
            variant=args.variant,
            headless=args.headless,
            keep_open=args.keep_open,
        )
    else:
        result = run_hybrid_demo(
            app_url=args.app_url,
            billing_email=args.email,
            mac_app=args.mac_app,
            headless=args.headless,
            keep_open=args.keep_open,
        )
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
