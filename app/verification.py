"""Surface-neutral condition DSL and deterministic final checkers."""
from __future__ import annotations

import json
import os
import re
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from .macro_skill import resolve_params


def location_path(location: str) -> Path:
    if location == "Desktop":
        return Path.home() / "Desktop"
    return Path(os.path.expanduser(location))


def _lookup(value: Any, path: str):
    current = value
    for part in path.split("."):
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current[part]
    return current


def docx_contains(path: Path, expected: str) -> bool:
    if not path.exists():
        return False
    try:
        xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8", "ignore")
        actual = " ".join(re.findall(r"<w:t[^>]*>([^<]+)</w:t>", xml))
        return expected in actual
    except (OSError, KeyError, zipfile.BadZipFile):
        return False


def evaluate_condition(condition: dict | None, state: dict, params: dict) -> tuple[bool, list[str]]:
    condition = resolve_params(condition or {}, params)
    failures: list[str] = []
    if not condition:
        return True, failures
    if "all" in condition:
        for child in condition["all"]:
            ok, child_failures = evaluate_condition(child, state, params)
            if not ok:
                failures.extend(child_failures)
        return not failures, failures
    if "any" in condition:
        attempts = [evaluate_condition(child, state, params) for child in condition["any"]]
        if not any(ok for ok, _ in attempts):
            failures.append("none of the alternative conditions matched")
            failures.extend(item for _, items in attempts for item in items)
        return not failures, failures
    if "not" in condition:
        ok, _ = evaluate_condition(condition["not"], state, params)
        return (not ok, [] if not ok else ["negated condition matched"])

    exact = {
        "foreground_app": "foreground_app", "url": "url", "title": "title",
        "clipboard": "clipboard", "word_document_count": "word_document_count",
    }
    for key, state_key in exact.items():
        if key in condition and state.get(state_key) != condition[key]:
            failures.append(f"{key} expected {condition[key]!r}, got {state.get(state_key)!r}")
    contains = {
        "app_window": "windows", "ui_text": "ui_text", "dialog": "ui_text",
        "url_contains": "url", "title_contains": "title", "text_contains": "visible_text",
        "clipboard_contains": "clipboard",
    }
    for key, state_key in contains.items():
        if key in condition and str(condition[key]).lower() not in str(state.get(state_key, "")).lower():
            failures.append(f"{key} not found: {condition[key]!r}")
    if condition.get("word_document") is True and state.get("word_document_count", 0) < 1:
        failures.append("Word has no open document")
    if condition.get("word_document") is False and state.get("word_document_count", 0) > 0:
        failures.append("Word document unexpectedly open")
    if condition.get("app_running") and condition["app_running"] not in state.get("running_apps", []):
        failures.append(f"application is not running: {condition['app_running']}")
    if condition.get("element_visible"):
        needle = str(condition["element_visible"])
        if needle.lower() not in str(state.get("visible_text", state.get("ui_text", ""))).lower():
            failures.append(f"element is not visible: {needle}")
    if condition.get("file_exists"):
        path = location_path(condition.get("location", params.get("location", "Desktop"))) / condition["file_exists"]
        if not path.exists():
            failures.append(f"file not found: {path}")
    if condition.get("state_equals"):
        for path, expected in condition["state_equals"].items():
            try:
                actual = _lookup(state, path)
            except (KeyError, IndexError, TypeError, ValueError):
                actual = None
            if actual != expected:
                failures.append(f"state {path} expected {expected!r}, got {actual!r}")
    return not failures, failures


def _http_json(checker: dict) -> tuple[bool, list[str]]:
    try:
        request = urllib.request.Request(checker["url"], method=checker.get("method", "GET"))
        with urllib.request.urlopen(request, timeout=float(checker.get("timeout", 5))) as response:
            data = json.load(response)
    except Exception as exc:
        return False, [f"HTTP checker failed: {type(exc).__name__}: {exc}"]
    failures = []
    for path, expected in checker.get("equals", {}).items():
        try:
            actual = _lookup(data, path)
        except (KeyError, IndexError, TypeError, ValueError):
            actual = None
        if actual != expected:
            failures.append(f"response {path} expected {expected!r}, got {actual!r}")
    return not failures, failures


def check_final(checker: dict | None, params: dict, state: dict | None = None) -> tuple[bool, list[str]]:
    checker = resolve_params(checker or {}, params)
    state = state or {}
    if not checker:
        return True, []
    kind = checker.get("type", "condition")
    if kind == "condition":
        return evaluate_condition(checker.get("condition", {}), state, params)
    if kind == "http_json":
        return _http_json(checker)
    if kind in {"all", "any"}:
        results = [check_final(child, params, state) for child in checker.get("checks", [])]
        success = all(ok for ok, _ in results) if kind == "all" else any(ok for ok, _ in results)
        return success, [] if success else [item for _, failures in results for item in failures]
    if kind in {"file", "text_file", "word_docx"}:
        location = location_path(checker.get("location", params.get("location", "Desktop")))
        path = location / checker.get("filename", params.get("filename", ""))
        failures = []
        if not path.exists():
            failures.append(f"output file missing: {path}")
        expected = checker.get("contains")
        values = expected if isinstance(expected, list) else [expected]
        for value in values:
            if value is None:
                continue
            if kind == "word_docx":
                matched = docx_contains(path, value)
            else:
                try:
                    matched = value in path.read_text(encoding=checker.get("encoding", "utf-8"))
                except OSError:
                    matched = False
            if not matched:
                failures.append(f"file does not contain expected text: {value!r}")
        return not failures, failures
    return False, [f"unknown checker type: {kind}"]
