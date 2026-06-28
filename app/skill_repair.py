"""Localized Gemini repair with candidate validation and success-gated promotion."""
from __future__ import annotations

import base64
import copy
import json
import os
import re
import subprocess
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from google import genai
from google.genai import types

from .config import CU_MODEL
from .local_skill_registry import LocalSkillRegistry
from .macro_skill import BROWSER_OPS, DESKTOP_OPS, resolve_params, validate_macro
from .verified_replay import replay_verified


MAX_REPAIR_STEPS = 6
REPAIR_MODEL = os.getenv("ROTE_REPAIR_MODEL", CU_MODEL)


class _OverlayRegistry:
    def __init__(self, base: LocalSkillRegistry, override: dict):
        self.base = base
        self.override = override

    def load_skill(self, name: str, version=None):
        if name == self.override["name"] and version is None:
            return copy.deepcopy(self.override)
        return self.base.load_skill(name, version)


def _clean_steps(value, params: dict, id_prefix: str = "repair", surface: str = "desktop") -> list[dict]:
    steps = value.get("replacement_steps") if isinstance(value, dict) else None
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_REPAIR_STEPS:
        raise ValueError(f"repair must contain 1-{MAX_REPAIR_STEPS} replacement_steps")
    cleaned = []
    allowed = (DESKTOP_OPS if surface == "desktop" else BROWSER_OPS) - {"call"}
    for index, raw in enumerate(steps, 1):
        if not isinstance(raw, dict) or raw.get("op") not in allowed:
            raise ValueError(f"invalid repair operation at index {index}")
        if any(key in raw for key in ("x", "y", "coords")):
            raise ValueError("coordinate-dependent repair rejected")
        encoded = json.dumps(raw)
        for name, literal in ((name, str(item)) for name, item in params.items() if item is not None):
            if literal and literal in encoded and "{{" + name + "}}" not in encoded:
                raise ValueError(f"repair hardcodes parameter {name}; use {{{{{name}}}}}")
        step = copy.deepcopy(raw)
        if surface == "browser" and step["op"] in {"click", "fill", "select", "check", "uncheck"}:
            target = step.get("target")
            if isinstance(target, str):
                target = {"text": target}
            elif isinstance(target, dict) and target.get("type") in {"role", "text", "label", "css", "testid"}:
                kind = target.pop("type")
                value = target.pop("value", target.pop("selector", target.get("name")))
                target = {kind: value, **target}
            if not target:
                selector = step.pop("selector", step.pop("css_selector", None))
                target_text = step.pop("target_text", step.pop("element", None))
                if selector:
                    target = {"css": selector}
                elif target_text:
                    target = {"text": target_text}
                elif step.get("role"):
                    target = {"role": step.pop("role"), "name": step.pop("name", None)}
            step["target"] = target
            if not isinstance(target, dict) or not any(k in target for k in ("role", "text", "label", "css", "testid")):
                raise ValueError("browser repair requires semantic targets")
        step.setdefault("id", f"{id_prefix}_repair_{index}_{step['op']}")
        step.setdefault("precondition", {})
        step.setdefault("postcondition", {})
        step.setdefault("timeout", 3)
        step.setdefault("retry_limit", 0)
        step.setdefault("fallback", [])
        cleaned.append(step)
    return cleaned


def _screenshot_bytes(backend) -> bytes | None:
    if hasattr(backend, "screenshot_png"):
        return backend.screenshot_png()
    try:
        from .desktop_cu import grab_screen
        encoded, _ = grab_screen()
        return base64.b64decode(encoded)
    except Exception:
        return None


def repair_failed_step(skill: dict, failure_context: dict, params: dict, *, backend=None, client=None) -> list[dict]:
    failure = failure_context.get("failure") or failure_context
    failed = failure.get("step", {})
    surface = skill.get("surface", "desktop")
    allowed = sorted((DESKTOP_OPS if surface == "desktop" else BROWSER_OPS) - {"call"})
    prompt = {
        "instruction": (
            "Repair only the failed macro transition. Return JSON with replacement_steps. "
            f"Surface is {surface}. Use only these operations: {allowed}; no coordinates. "
            "Browser actions must use role/text/label/css/testid semantic targets. Preserve {{param}} "
            "references. The final replacement step must satisfy the failed postcondition."
        ),
        "skill": skill["name"],
        "failed_step": {key: value for key, value in failed.items() if not key.startswith("_")},
        "failure_reason": failure.get("reason"),
        "condition_failures": failure.get("postcondition_failures") or failure.get("precondition_failures"),
        "current_state": failure.get("state", {}),
        "successful_steps": [
            {"step_id": record["step_id"], "op": record["op"]}
            for record in failure_context.get("records", []) if record.get("success")
        ],
        "params": {name: "{{" + name + "}}" for name in params},
        "max_steps": MAX_REPAIR_STEPS,
    }
    client = client or genai.Client()
    contents = [types.Part.from_text(text=json.dumps(prompt, indent=2))]
    screenshot = _screenshot_bytes(backend)
    if screenshot:
        contents.append(types.Part.from_bytes(data=screenshot, mime_type="image/png"))
    response = client.models.generate_content(
        model=REPAIR_MODEL,
        contents=contents,
        config={"response_mime_type": "application/json"},
    )
    text = re.sub(r"^```(?:json)?|```$", "", response.text.strip(), flags=re.MULTILINE).strip()
    return _clean_steps(json.loads(text), params, failed.get("id", "repair"), surface)


def _replace_step(owner: dict, step_id: str, replacement: list[dict]) -> dict:
    patched = copy.deepcopy(owner)
    for index, step in enumerate(patched["steps"]):
        if step["id"] == step_id:
            patched["steps"][index:index + 1] = replacement
            validate_macro(patched)
            return patched
    raise ValueError(f"failed step not found in source skill: {step_id}")


def reset_word(params: dict) -> None:
    subprocess.run(
        ["osascript", "-e", 'tell application "Microsoft Word" to quit saving no'],
        check=False, capture_output=True,
    )
    time.sleep(1)
    location = params.get("location", "Desktop")
    folder = Path.home() / "Desktop" if location == "Desktop" else Path(os.path.expanduser(location))
    filename = params.get("filename")
    if filename:
        for path in (folder / f"{filename}.docx", folder / f"~${filename}.docx"):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def reset_stale_word(params: dict) -> None:
    """Create the deterministic drift state: Word is frontmost with zero open documents."""
    reset_word(params)
    subprocess.run(["open", "-a", "Microsoft Word"], check=False, capture_output=True)
    time.sleep(2)
    subprocess.run(
        ["osascript", "-e", 'tell application "Microsoft Word" to close every document saving no'],
        check=False, capture_output=True,
    )
    subprocess.run(
        ["osascript", "-e", 'tell application "Microsoft Word" to activate'],
        check=False, capture_output=True,
    )
    time.sleep(1)


class _FirstHeadingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_h1 = False
        self._parts: list[str] = []
        self.heading = ""

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "h1" and not self.heading:
            self._in_h1 = True
            self._parts = []

    def handle_data(self, data):
        if self._in_h1:
            self._parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "h1" and self._in_h1:
            self.heading = " ".join("".join(self._parts).split())
            self._in_h1 = False


def _fetch_heading(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            html = response.read(500_000).decode("utf-8", "ignore")
    except Exception:
        return ""
    parser = _FirstHeadingParser()
    parser.feed(html)
    if parser.heading:
        return parser.heading
    title = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    return " ".join(title.group(1).split()) if title else ""


def _set_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text, text=True, check=False, timeout=5)


def reset_stale_textedit_note(params: dict) -> None:
    """Create the non-Acme drift state: a real page is visible, its heading is on the pasteboard,
    and TextEdit is frontmost with zero open documents. The stale skill assumes a note already
    exists, so paste fails until repair inserts the missing new-document transition."""
    url = params.get("source_url", "https://www.iana.org/help/example-domains")
    heading = _fetch_heading(url) or params.get("heading", "Example Domains")
    params["heading"] = heading
    subprocess.run(["open", url], check=False, capture_output=True)
    _set_clipboard(heading)
    subprocess.run(["osascript", "-e", 'tell application "TextEdit" to quit saving no'],
                   check=False, capture_output=True)
    time.sleep(1)
    subprocess.run(["open", "-a", "TextEdit"], check=False, capture_output=True)
    time.sleep(1)
    subprocess.run(
        ["osascript",
         "-e", 'tell application "TextEdit" to close every document saving no',
         "-e", 'tell application "TextEdit" to activate'],
        check=False, capture_output=True,
    )
    time.sleep(0.5)


class RepairService:
    def __init__(self, registry: LocalSkillRegistry | None = None, reset=reset_word, client=None):
        self.registry = registry or LocalSkillRegistry()
        self.reset = reset
        self.client = client

    def repair_and_validate(self, root_skill: dict, params: dict, failure_result: dict,
                            *, backend, on_event=None) -> dict:
        failure = failure_result["failure"]
        owner_name = failure["step"].get("_source_skill", root_skill["name"])
        owner = root_skill if owner_name == root_skill["name"] else self.registry.load_skill(owner_name)
        replacement = repair_failed_step(owner, failure_result, params, backend=backend, client=self.client)
        patched = _replace_step(owner, failure["step_id"], replacement)
        candidate = self.registry.create_candidate(patched, reason=f"repair:{failure['step_id']}")
        overlay = _OverlayRegistry(self.registry, candidate)
        if on_event:
            on_event("validating", {"candidate": candidate})
        self.reset(params)
        root_candidate = candidate if owner_name == root_skill["name"] else root_skill
        validation = replay_verified(
            root_candidate, params, allow_repair=False, backend=backend,
            registry=overlay, on_event=on_event,
        )
        if validation["success"] and validation["checker_passed"]:
            promoted = self.registry.promote(candidate, validation)
            validation.update({
                "promoted": True,
                "promoted_skill": promoted["name"],
                "promoted_version": promoted["version"],
                "skill_version": root_candidate.get("version", 1),
            })
            if on_event:
                on_event("promoted", validation)
            return validation
        self.registry.reject(candidate, "; ".join(validation.get("checker_failures", [])) or "validation failed")
        validation.update({"promoted": False, "rejected_version": candidate["version"]})
        if on_event:
            on_event("rejected", validation)
        return validation


def promote(candidate: dict, validation: dict, registry: LocalSkillRegistry | None = None) -> dict:
    return (registry or LocalSkillRegistry()).promote(candidate, validation)
