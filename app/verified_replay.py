"""Condition-aware, model-free replay for desktop macro skills."""
from __future__ import annotations

import os
import re
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Callable

from .local_skill_registry import LocalSkillRegistry
from .macro_skill import migrate_macro, resolve_params
from .verification import check_final as _check_final
from .verification import docx_contains as _docx_contains
from .verification import evaluate_condition as _evaluate_condition


class MacOSDesktopBackend:
    """Local deterministic backend. It never calls a model or captures a screenshot."""

    def execute(self, step: dict) -> dict:
        import pyautogui
        from .desktop_cu import _KEYMAP, ensure_app, settle

        op = step["op"]
        if op == "open_app":
            return {"message": ensure_app(step["app"], float(step.get("launch_wait", 6)))}
        if op == "quit_app":
            subprocess.run(["osascript", "-e", f'tell application "{step["app"]}" to quit'],
                           check=False, capture_output=True)
            return {}
        if op == "wait":
            return {"settled_s": settle(max_wait=float(step.get("seconds", 2)))}
        if op == "hotkey":
            pyautogui.hotkey(*[_KEYMAP.get(key.lower(), key.lower()) for key in step["keys"]])
        elif op == "key":
            pyautogui.press(_KEYMAP.get(step["key"].lower(), step["key"].lower()))
        elif op == "type":
            pyautogui.write(step["text"], interval=0.01)
        else:
            raise ValueError(f"backend cannot execute op: {op}")
        settle(max_wait=min(float(step.get("timeout", 3)), 3.0))
        return {}

    @staticmethod
    def _osa(script: str) -> str:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def inspect(self) -> dict:
        foreground = self._osa(
            'tell application "System Events" to get name of first application process whose frontmost is true'
        )
        windows = self._osa(
            'tell application "System Events" to tell first application process whose frontmost is true '
            'to get name of every window'
        )
        ui_text = self._osa(
            'tell application "System Events" to tell first application process whose frontmost is true '
            'to get description of every UI element of every window'
        )
        word_docs = self._osa(
            'if application "Microsoft Word" is running then tell application "Microsoft Word" '
            'to return count of documents'
        )
        textedit_docs = self._osa(
            'if application "TextEdit" is running then tell application "TextEdit" '
            'to return count of documents'
        )
        textedit_text = self._osa(
            'if application "TextEdit" is running then tell application "TextEdit" '
            'to if (count of documents) > 0 then return text of front document'
        )
        running = self._osa('tell application "System Events" to get name of every application process')
        clipboard = self._osa('the clipboard as text')
        return {
            "foreground_app": foreground,
            "windows": windows,
            "ui_text": ui_text,
            "word_document_count": int(word_docs) if word_docs.isdigit() else 0,
            "textedit_document_count": int(textedit_docs) if textedit_docs.isdigit() else 0,
            "textedit_text": textedit_text,
            "running_apps": [item.strip() for item in running.split(",") if item.strip()],
            "clipboard": clipboard,
        }


def _location_path(location: str) -> Path:
    if location == "Desktop":
        return Path.home() / "Desktop"
    return Path(os.path.expanduser(location))


def _ensure_unique_filename(params: dict) -> dict:
    """Never overwrite: if <filename>.docx already exists at <location>, bump to <filename>_2,
    _3, ... (macOS-style) so the new run keeps the user's previous files. Computed ONCE up front
    so the save step, the verify step, and the final checker all use the same unique name."""
    fname = params.get("filename")
    if not fname:
        return params
    location = _location_path(params.get("location", "Desktop"))
    if not (location / f"{fname}.docx").exists():
        return params
    i = 2
    while (location / f"{fname}_{i}.docx").exists():
        i += 1
    return {**params, "filename": f"{fname}_{i}"}


def docx_contains(path: Path, expected: str) -> bool:
    return _docx_contains(path, expected)


def evaluate_condition(condition: dict | None, state: dict, params: dict) -> tuple[bool, list[str]]:
    return _evaluate_condition(condition, state, params)


def check_final(checker: dict | None, params: dict, state: dict | None = None) -> tuple[bool, list[str]]:
    return _check_final(checker, params, state)


def _expand_steps(skill: dict, params: dict, registry: LocalSkillRegistry) -> list[dict]:
    expanded = []
    for step in skill["steps"]:
        if step["op"] != "call":
            item = resolve_params(step, params)
            item["_source_skill"] = skill["name"]
            item["_source_version"] = skill.get("version", 1)
            expanded.append(item)
            continue
        child = registry.load_skill(step["skill"], step.get("version"))
        child_params = {**child.get("params", {})}
        child_params.update(resolve_params(step.get("params", {}), params))
        for item in _expand_steps(child, child_params, registry):
            item["_call_step_id"] = step["id"]
            expanded.append(item)
    return expanded


def replay_verified(
    skill: dict,
    params: dict | None = None,
    allow_repair: bool = False,
    *,
    backend=None,
    registry: LocalSkillRegistry | None = None,
    repair_service=None,
    on_event: Callable[[str, dict], None] | None = None,
    optimistic: bool = False,
) -> dict:
    """Replay and verify a macro. Repair is delegated once when explicitly enabled.

    Default (optimistic=False) is the VERIFIED contract: per-step pre/postconditions, retry and
    fallback accounting, stop-at-failed-step, and a final check against live desktop state. Every
    self-improvement / repair / validation caller depends on this — do NOT change the default.

    optimistic=True is an OPT-IN happy-path speedup for user-facing replay (voice HUD, plain
    --replay): execute every step blind with dynamic waits and NO per-step inspection, then verify
    ONCE with the final checker. The slow per-step verification still runs to DIAGNOSE a real
    failure, but only when repair is also requested. A healthy skill replays at full speed."""
    started = time.time()
    skill = migrate_macro(skill)
    params = {**skill.get("params", {}), **(params or {})}
    params = _ensure_unique_filename(params)             # keep old files; save as name_2, name_3, ...
    backend = backend or MacOSDesktopBackend()
    registry = registry or LocalSkillRegistry()
    records = []
    failed = None
    retries = fallbacks = 0
    steps = _expand_steps(skill, params, registry)

    # ---- OPTIMISTIC FAST PATH: run blind with dynamic waits, verify once at the end ----
    if optimistic:
        for index, step in enumerate(steps, 1):
            if on_event:
                on_event("step", {"index": index, "total": len(steps), "step": step})
            backend.execute(step)                          # dynamic waits inside; no inspection
        # file/HTTP checkers don't need desktop state -> skip the expensive final inspect() scan
        passed, failures = check_final(skill.get("checker"), params)
        if passed or not (allow_repair and repair_service is not None):
            return {
                "success": passed, "checker_passed": passed,
                "checker_failures": [] if passed else failures,
                "failed_step_id": None, "failure": None, "records": [],
                "steps": len(steps), "elapsed_s": round(time.time() - started, 2),
                "retries": 0, "fallbacks": 0, "model_calls": 0, "repair_calls": 0,
                "skill_name": skill["name"], "skill_version": skill.get("version", 1),
                "used_skill": True, "mode": "optimistic",
                "filename": params.get("filename"), "location": params.get("location", "Desktop"),
            }
        if on_event:                                       # failed + repair requested -> diagnose below
            on_event("diagnosing", {"checker_failures": failures})

    # ---- VERIFIED DIAGNOSTIC PATH (per-step pre/postconditions to localize + repair) ----
    for index, step in enumerate(steps, 1):
        if on_event:
            on_event("step", {"index": index, "total": len(steps), "step": step})
        state_before = backend.inspect()
        pre_ok, pre_failures = evaluate_condition(step.get("precondition"), state_before, params)
        record = {"step_id": step["id"], "op": step["op"], "precondition": pre_ok,
                  "precondition_failures": pre_failures, "source_skill": step.get("_source_skill")}
        if not pre_ok:
            record["success"] = False
            records.append(record)
            failed = {**record, "step": step, "state": state_before, "reason": "precondition"}
            break

        attempts = int(step.get("retry_limit", 0)) + 1
        post_ok = False
        post_failures = []
        for attempt in range(attempts):
            record["execution"] = backend.execute(step)
            state_after = backend.inspect()
            post_ok, post_failures = evaluate_condition(step.get("postcondition"), state_after, params)
            if post_ok:
                break
            if attempt + 1 < attempts:
                retries += 1
        if not post_ok:
            for fallback in step.get("fallback", []):
                fallback = resolve_params(fallback, params)
                backend.execute(fallback)
                fallbacks += 1
            if step.get("fallback"):
                state_after = backend.inspect()
                post_ok, post_failures = evaluate_condition(step.get("postcondition"), state_after, params)
        record.update({"postcondition": post_ok, "postcondition_failures": post_failures, "success": post_ok})
        records.append(record)
        if not post_ok:
            failed = {**record, "step": step, "state": state_after, "reason": "postcondition"}
            break

    checker_passed, checker_failures = (False, ["step execution failed"])
    if failed is None:
        checker_passed, checker_failures = check_final(skill.get("checker"), params, backend.inspect())
    result = {
        "success": failed is None and checker_passed,
        "checker_passed": checker_passed,
        "checker_failures": checker_failures,
        "failed_step_id": failed.get("step_id") if failed else None,
        "failure": failed,
        "records": records,
        "steps": len(records),
        "elapsed_s": round(time.time() - started, 2),
        "retries": retries,
        "fallbacks": fallbacks,
        "model_calls": 0,
        "repair_calls": 0,
        "skill_name": skill["name"],
        "skill_version": skill.get("version", 1),
        "used_skill": True,
        "mode": "verified_replay",
        "filename": params.get("filename"),
        "location": params.get("location", "Desktop"),
    }
    if not result["success"] and allow_repair and repair_service is not None:
        if on_event:
            on_event("repairing", result)
        try:
            repaired = repair_service.repair_and_validate(
                skill, params, result, backend=backend, on_event=on_event,
            )
        except Exception as exc:
            result.update({
                "repair_error": f"{type(exc).__name__}: {exc}",
                "repair_calls": 1,
                "model_calls": 1,
                "promoted": False,
            })
            if on_event:
                on_event("rejected", result)
            return result
        repaired["repair_calls"] = 1
        repaired["model_calls"] = repaired.get("model_calls", 0) + 1
        return repaired
    return result


def validate_candidate(candidate: dict, params: dict, reset, checker=None, **kwargs) -> dict:
    reset(params)
    result = replay_verified(candidate, params, allow_repair=False, **kwargs)
    if checker is not None:
        passed = bool(checker(candidate, params))
        result["checker_passed"] = passed
        result["success"] = result["success"] and passed
    return result
