"""skill_compiler — the SECOND model. It reads the 'doer' model's intent log (a recorded
computer-use trajectory) and writes a keyboard-first replay macro. The macro content is
authored entirely by Gemini 3.5 here — not by a human and not hand-written.

  python -m app.skill_compiler --trace traces/desktop_trajectory.json --out database/skills/learned.macro.json
"""
import os
import re
import json
import argparse
from google import genai

from .config import CU_MODEL

# the compiler runs on Gemini 3.5 (a reasoning pass over the log), separate from the CU 'doer'.
COMPILER_MODEL = os.getenv("ROTE_COMPILER_MODEL", CU_MODEL)

SCHEMA = """You output ONLY a JSON object with this exact shape:
{
  "name": "<short_snake_case_task_name>",
  "app": "<the macOS app driven, e.g. Microsoft Word>",
  "os": "macos",
  "note": "<one line on what this macro does>",
  "params": { "<param_name>": "<default value used in this recording>", ... },
  "variables": {
    "<param_name>": { "type": "string|number|integer|boolean", "required": true|false }
  },
  "steps": [ { "op": "...", ...fields..., "why": "<short reason>" }, ... ]
}

Allowed step ops (keyboard-first — avoid coordinate clicks):
  {"op":"open_app","app":"Microsoft Word","launch_wait":6,"why":"..."}   focus an app. SELF-CHECKING:
        if the app is already open it just comes to the front instantly; if it is closed it launches
        and waits launch_wait seconds. Use this before touching ANY app — including switching back.
  {"op":"hotkey","keys":["command","s"],"why":"..."}        a shortcut chord
  {"op":"key","key":"return","why":"..."}                   a single key
  {"op":"type","text":"...","why":"..."}                    type literal text
  {"op":"wait","seconds":2,"why":"..."}                     pause for the app to catch up
"""

INSTRUCTIONS = """You are a skill compiler. Below is a recorded computer-use trajectory: a list
of steps a vision model took to complete a desktop task, each with its own `intent`.

Turn it into a RELIABLE, keyboard-first replay macro that reproduces the SAME end result with
NO screenshots and NO model calls. Rules:
- Open apps with {"op":"open_app"} — never Spotlight/Command+Space (synthetic Cmd+Space is unreliable).
- Replace visual clicks with keyboard shortcuts wherever one exists: Command+N (new document),
  Command+S (save), Command+B (bold), Command+D (Desktop in the save dialog), Return (confirm).
- DROP redundant or failed fumbles in the recording (e.g. duplicate clicks, retries that did nothing).
- Extract user-controlled values into variables and reference them as {{text}}, {{filename}},
  or another descriptive name. For each extracted variable, populate the top-level "variables"
  object with its name as key, a "type" (string / number / integer / boolean), and
  "required": true if the task cannot proceed without it, false if it is optional. ALSO populate
  the top-level "params" object mapping each variable name to the actual literal value used in
  this recording. "params" gives the replay engine default values for {{placeholders}};
  "variables" describes their types. Emit BOTH for every variable.
- APP GUARDRAIL: never assume an app is open. Before interacting with ANY app — and again every
  time you switch back to an app you used earlier — emit {"op":"open_app","app":"<Name>","launch_wait":6}.
  That op self-checks (already-open -> instant focus; closed -> launch + wait), so:
    * do NOT add a separate {"op":"wait"} right after an open_app (the wrapper already waited), and
    * do NOT use Command+Tab or clicks to switch apps — use open_app instead.
- TIMING (replay has no screenshots to wait on, so other waits must be generous):
    * after Command+N / selecting a blank document, wait 2 seconds.
    * after Command+S opens a save dialog, wait 2 seconds before typing/shortcuts.
    * after a paste or a destination-change shortcut, wait 1 second.
  When unsure, wait LONGER — a replay that moves too fast silently fails.
- Output ONLY the JSON object. No markdown, no commentary.

RECORDED TRAJECTORY:
"""


def compile_macro(trace: dict) -> dict:
    client = genai.Client()
    log = [{"intent": s.get("intent", ""), "action": s.get("action", ""),
            "args": {k: v for k, v in s.get("args", {}).items() if k != "intent"}}
           for s in trace.get("steps", [])]
    prompt = (SCHEMA + "\n" + INSTRUCTIONS
              + json.dumps({"task": trace.get("intent", ""), "steps": log}, indent=2))
    resp = client.models.generate_content(
        model=COMPILER_MODEL, contents=prompt,
        config={"response_mime_type": "application/json"})
    text = resp.text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()  # strip stray fences
    return json.loads(text)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True, help="intent log JSON written by app.desktop_cu --trace")
    ap.add_argument("--out", required=True, help="where to write the Gemini-authored macro")
    a = ap.parse_args()

    trace = json.load(open(a.trace))
    print(f"compiling {len(trace.get('steps', []))} recorded steps with {COMPILER_MODEL} ...")
    macro = compile_macro(trace)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(macro, f, indent=2)
    print(f"\nGemini-authored macro ({len(macro.get('steps', []))} steps) -> {a.out}\n")
    print(json.dumps(macro, indent=2))
