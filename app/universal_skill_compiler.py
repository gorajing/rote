"""Compile a successful CU trace into a verified macro for desktop or browser surfaces."""
from __future__ import annotations

import argparse
import json
import os
import re

from google import genai

from .config import CU_MODEL
from .macro_skill import BROWSER_OPS, DESKTOP_OPS, migrate_macro, validate_macro


MODEL = os.getenv("ROTE_COMPILER_MODEL", CU_MODEL)


def compile_macro(trace: dict, surface: str) -> dict:
    if surface not in {"desktop", "browser"}:
        raise ValueError("surface must be desktop or browser")
    operations = sorted(DESKTOP_OPS if surface == "desktop" else BROWSER_OPS)
    grounding = (
        "Use keyboard-first operations and application names."
        if surface == "desktop" else
        "Replace coordinates with semantic targets using role+name, label, text, testid, or css."
    )
    prompt = f"""You compile a verified Computer Use trace into a reusable macro.
Output only JSON. Surface: {surface}. Allowed operations: {operations}.
{grounding}

Required top-level fields: schema_version=2, surface, name, app, os, version=1,
parent_version=null, status=active, note, params, checker, stats, steps.
Each step requires a stable semantic id, op, precondition, postcondition, timeout,
retry_limit, fallback, and why. Extract user values into params and use {{{{param}}}}
references. Never emit x/y/coords. Browser targets must be semantic.

Supported conditions include foreground_app, app_running, app_window, ui_text,
word_document, clipboard_contains, url_contains, title_contains, text_contains,
element_visible, file_exists, state_equals, and all/any/not composition.
Supported final checkers include condition, file, text_file, word_docx, http_json,
and all/any composition. Prefer externally verifiable state over visual self-report.

TRACE:
{json.dumps(trace, indent=2)}
"""
    response = genai.Client().models.generate_content(
        model=MODEL, contents=prompt, config={"response_mime_type": "application/json"},
    )
    text = re.sub(r"^```(?:json)?|```$", "", response.text.strip(), flags=re.MULTILINE).strip()
    macro = migrate_macro(json.loads(text))
    macro["surface"] = surface
    validate_macro(macro)
    return macro


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile a CU trace into a verified macro")
    parser.add_argument("--trace", required=True)
    parser.add_argument("--surface", required=True, choices=("desktop", "browser"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    with open(args.trace, encoding="utf-8") as source:
        macro = compile_macro(json.load(source), args.surface)
    with open(args.out, "w", encoding="utf-8") as destination:
        json.dump(macro, destination, indent=2)


if __name__ == "__main__":
    main()
