"""Compile a desktop Computer Use intent log into a keyboard-first replay macro.

Usage:
  python -m app.desktop_skill_compiler --trace traces/desktop_trajectory.json \
      --out database/skills/learned.macro.json
"""
import argparse
import json
import os
import re

from google import genai

from .config import CU_MODEL
from .macro_skill import migrate_macro, validate_macro


COMPILER_MODEL = os.getenv("ROTE_COMPILER_MODEL", CU_MODEL)

SCHEMA = """You output ONLY a JSON object with this exact shape:
{
  "schema_version": 2,
  "surface": "desktop_or_browser",
  "name": "<short_snake_case_task_name>",
  "app": "<the macOS app driven, e.g. Microsoft Word>",
  "os": "macos",
  "version": 1,
  "parent_version": null,
  "status": "active",
  "note": "<one line on what this macro does>",
  "params": { "<param>": "<value used>", ... },
  "checker": {
    "type": "word_docx",
    "location": "{{location}}",
    "filename": "{{filename}}.docx",
    "contains": "{{text}}"
  },
  "stats": {"uses": 0, "successes": 0, "failures": 0, "success_rate": 0.0,
            "avg_duration": 0.0, "model_calls": 0},
  "steps": [
    {
      "id": "<stable_semantic_id>",
      "op": "...",
      "...op fields...": "...",
      "precondition": {},
      "postcondition": {},
      "timeout": 3,
      "retry_limit": 0,
      "fallback": [],
      "why": "<short reason>"
    }
  ]
}

Allowed step ops (keyboard-first -- avoid coordinate clicks):
  {"op":"open_app","app":"Microsoft Word","launch_wait":6,"why":"..."}
        focus an app. If it is already open it comes to the front; otherwise it launches
        and waits up to launch_wait seconds. Use this before touching or switching to any app.
  {"op":"hotkey","keys":["command","s"],"why":"..."}  a shortcut chord
  {"op":"key","key":"return","why":"..."}             a single key
  {"op":"type","text":"...","why":"..."}              type literal text
  {"op":"wait","seconds":2,"why":"..."}                pause for the app to catch up

For browser workflows use surface "browser", include a start_url, and use only semantic steps:
  {"op":"navigate","url":"https://...","why":"..."}
  {"op":"click","target":{"role":"link","text":"..."},"why":"..."}
  {"op":"fill","target":{"label":"Search"},"text":"{{query}}","why":"..."}
  {"op":"press","key":"Enter","why":"..."}
Browser targets must use role/text/label/css/testid and must never contain coordinates.
"""

INSTRUCTIONS = """You are a skill compiler. Below is a recorded computer-use trajectory: a list
of steps a vision model took to complete a desktop task, each with its own `intent`.

Turn it into a RELIABLE, keyboard-first replay macro that reproduces the SAME end result with
NO screenshots and NO model calls. Rules:
- Open apps with {"op":"open_app"} -- never Spotlight/Command+Space.
- Replace visual clicks with keyboard shortcuts wherever one exists: Command+N (new document),
  Command+S (save), Command+B (bold), Command+D (Desktop in the save dialog), Return (confirm).
- DROP redundant or failed fumbles in the recording.
- Extract user-controlled values into params and reference them as {{text}}, {{filename}},
  {{location}}, or another descriptive parameter. Never repeat those literals in steps.
- Give every step a stable semantic id that will remain meaningful when nearby steps change.
- Add deterministic preconditions and postconditions where macOS can inspect them. Supported
  conditions are foreground_app, app_window, word_document, ui_text, dialog, and file_exists.
- For Word output, add a word_docx checker that verifies the parameterized filename and content.
- For browser output, add a condition checker for a deterministic final URL, title, or visible text.
- For other desktop output, add a file/text_file checker or an explicit reset adapter plus a
  deterministic condition/http_json checker. If the result cannot be checked deterministically,
  return a macro with an empty checker; it will be retained but never promoted.
- Set surface to browser only for a workflow that can be replayed with semantic browser targets;
  otherwise use desktop keyboard operations.
- Never assume an app is open. Before interacting with any app, and whenever switching back to
  one, emit {"op":"open_app","app":"<Name>","launch_wait":6}. Do not add a separate wait after
  open_app, use Command+Tab, or click to switch apps.
- After Command+N or selecting a blank document, wait 2 seconds.
- After Command+S, wait 2 seconds before typing or shortcuts.
- After a paste or destination-change shortcut, wait 1 second.
- Output ONLY the JSON object. No markdown, no commentary.

RECORDED TRAJECTORY:
"""


def compile_macro(trace: dict) -> dict:
    client = genai.Client()
    log = [
        {
            "intent": step.get("intent", ""),
            "action": step.get("action", ""),
            "args": {key: value for key, value in step.get("args", {}).items() if key != "intent"},
        }
        for step in trace.get("steps", [])
    ]
    prompt = (
        SCHEMA
        + "\n"
        + INSTRUCTIONS
        + json.dumps({"task": trace.get("intent", ""), "steps": log}, indent=2)
    )
    response = client.models.generate_content(
        model=COMPILER_MODEL,
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    text = response.text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    macro = migrate_macro(json.loads(text))
    validate_macro(macro)
    return macro


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", required=True, help="intent log JSON written by app.desktop_cu")
    parser.add_argument("--out", required=True, help="destination for the Gemini-authored macro")
    args = parser.parse_args()

    with open(args.trace, encoding="utf-8") as trace_file:
        trajectory = json.load(trace_file)
    print(f"compiling {len(trajectory.get('steps', []))} recorded steps with {COMPILER_MODEL} ...")
    macro = compile_macro(trajectory)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as output_file:
        json.dump(macro, output_file, indent=2)
    print(f"\nGemini-authored macro ({len(macro.get('steps', []))} steps) -> {args.out}\n")
    print(json.dumps(macro, indent=2))
