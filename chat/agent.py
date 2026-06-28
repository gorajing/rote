"""Rote chatbot agent — Gemini function-calling loop.

The agent manages four tools:
  search_db      always called first; vector-searches MongoDB for a matching skill
  execute_skill  replays a skill loaded from MongoDB (all scripts come from the DB)
  computer_use   fallback: drives the real desktop with Gemini CU and compiles a new skill
  put_db         persists the newly compiled skill so future runs can reuse it

Workflow enforced by the system prompt:
  user request → search_db → exact match? → execute_skill
                                          ↓ no
                                    computer_use → put_db
"""
import json
import sys
from pathlib import Path

from google import genai
from google.genai import types
from rich.console import Console
from rich.panel import Panel
from rich import box

_console = Console(highlight=False)

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from app.config import CU_MODEL
from chat.tools import search_db, execute_skill, computer_use, put_db


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are Rote, a desktop automation assistant for macOS.
You maintain a MongoDB library of verified automation skills and use it to perform tasks on the user's computer.

## Workflow — follow this order for every user request

1. **Always call `search_db` first.** Pass a concise natural-language description of the task.

2. **Evaluate the results — apply the two exact-match tests below to every returned skill.**

   A skill is a valid match only if it passes **both** tests:

   a. **Same program / application**: the skill's `site` and `platform` must match
      exactly the application or website the user is asking about.
      Examples of failures: the user says "Amazon" and the skill is for "eBay";
      the user says "Apple Mail" and the skill is for "Gmail"; the user says
      "Excel" and the skill is for "Google Sheets". Any platform mismatch → not a match.

   b. **Same task goal**: the skill must perform the identical operation, not just a
      similar one. "Buy headphones" ≠ "track an order". "Send a message" ≠ "create a group".
      If the action the skill performs is different from what the user is asking, even on
      the same app, it is not a match.

   Decision:
   - If exactly one skill passes both tests → call `execute_skill` with it and the `params`
     dict you build from the user's message. `params` must supply a value for every key
     listed as `required: true` in `skill.variables`.
   - If multiple skills pass both tests → pick the one with the highest `score`;
     if scores are equal, prefer the skill with more steps (more detailed).
   - If **no** skill passes both tests (or `search_db` returns nothing) →
     call `computer_use` directly. Do not try to adapt a partial match.

3. **After `computer_use` completes:**
   → Immediately call `put_db` with the `skill` field from the result to persist it for future reuse.

4. **Report back** in one short paragraph: which path was taken (DB match / live CU), which skill was used or created, what params were filled, and whether it succeeded or failed. If you used a DB skill, name it and state why it was an exact match.

## Execution contract
- All execution scripts come from MongoDB. `execute_skill` runs steps loaded from the DB.
- `computer_use` is the fallback that drives the real screen. It always produces a new skill that must be saved with `put_db`.
- If `execute_skill` returns `success: false`, report the failure and the `failed_step_id` verbatim. Do not retry automatically — ask the user what to do next.
- Never invent param values. If the user's message does not contain a required param, ask before calling any execution tool.
- When in doubt about whether a match is exact, default to `computer_use`. A false positive (running the wrong skill) is worse than a false negative (re-running CU).

## Style
- No preamble, no filler. Be direct.
- When asking for clarification, ask only what is strictly needed to fill missing params.
"""


# ---------------------------------------------------------------------------
# Tool declarations (JSON Schema)
# ---------------------------------------------------------------------------

_DECLARATIONS = [
    {
        "name": "search_db",
        "description": (
            "Vector-search the MongoDB skill library using a natural-language description. "
            "Returns up to 3 results, each with: name, description, site, platform, steps (the execution script), "
            "variables (required inputs as {name: {type, required}}), and a similarity score (0–1). "
            "Always call this before anything else."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Natural-language description of the task to look up.",
                }
            },
            "required": ["description"],
        },
    },
    {
        "name": "execute_skill",
        "description": (
            "Replay a skill from MongoDB on the macOS desktop using deterministic, model-free execution. "
            "Pass the full skill object returned by search_db and any runtime params from the user's request. "
            "Runtime params override the skill's stored defaults."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "object",
                    "description": "Full skill object as returned by search_db (all fields, including steps).",
                    "properties": {},
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Runtime param overrides extracted from the user's message. "
                        "Example: {\"filename\": \"report\", \"text\": \"Hello world\"}."
                    ),
                    "properties": {},
                },
            },
            "required": ["skill"],
        },
    },
    {
        "name": "computer_use",
        "description": (
            "Use Gemini Computer Use to perform a task on the macOS desktop when no DB skill matches. "
            "Controls the real screen (mouse, keyboard, screenshots) live. "
            "Records the execution and compiles it into a reusable skill. "
            "Always call put_db with the returned skill immediately after."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "description": (
                        "Clear, self-contained description of the task to perform. "
                        "Include all specifics (file names, text to type, app to use)."
                    ),
                }
            },
            "required": ["intent"],
        },
    },
    {
        "name": "put_db",
        "description": (
            "Persist a compiled skill to MongoDB so it can be found by future search_db calls. "
            "Must be called after every successful computer_use run."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "object",
                    "description": "The compiled skill dict from the 'skill' field of computer_use's result.",
                    "properties": {},
                }
            },
            "required": ["skill"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

_TOOL_MAP = {
    "search_db":     lambda a: search_db(a["description"]),
    "execute_skill": lambda a: execute_skill(a["skill"], a.get("params")),
    "computer_use":  lambda a: computer_use(a["intent"]),
    "put_db":        lambda a: put_db(a["skill"]),
}


def _dispatch(name: str, args: dict) -> dict:
    fn = _TOOL_MAP.get(name)
    if fn is None:
        return {"error": f"unknown tool: {name}"}
    try:
        result = fn(args)
        # Ensure everything is JSON-serializable before sending back to the model
        return json.loads(json.dumps(result, default=str))
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _abbrev(obj, limit: int = 160) -> str:
    s = json.dumps(obj, default=str, ensure_ascii=False)
    return s[:limit] + "…" if len(s) > limit else s

_TOOL_ICONS = {
    "search_db":     "🔍",
    "execute_skill": "▶",
    "computer_use":  "🖥",
    "put_db":        "💾",
}

def _print_call(name: str, args: dict) -> None:
    icon = _TOOL_ICONS.get(name, "⚙")
    _console.print(f"  {icon}  [bold cyan]{name}[/]  [dim]{_abbrev(args)}[/]")

def _print_result(result: dict) -> None:
    if isinstance(result, dict) and result.get("error"):
        _console.print(f"     [red]✗[/]  [dim]{_abbrev(result)}[/]")
    else:
        _console.print(f"     [green]✓[/]  [dim]{_abbrev(result)}[/]")


# ---------------------------------------------------------------------------
# Agent builder
# ---------------------------------------------------------------------------

def build_agent(model: str = CU_MODEL):
    """Return (client, model, GenerateContentConfig) ready for use."""
    client = genai.Client()
    tool = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name=d["name"],
                description=d["description"],
                parameters=d["parameters"],
            )
            for d in _DECLARATIONS
        ]
    )
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[tool],
        temperature=0.1,
    )
    return client, model, config


# ---------------------------------------------------------------------------
# Agentic loop
# ---------------------------------------------------------------------------

def run_turn(client, model: str, config, history: list) -> list:
    """Drive one user turn through the tool-call loop until the model stops using tools."""
    while True:
        with _console.status("[dim]thinking…[/]", spinner="dots"):
            response = client.models.generate_content(
                model=model,
                contents=history,
                config=config,
            )
        model_content = response.candidates[0].content
        history.append(model_content)

        tool_calls = [
            p for p in model_content.parts
            if hasattr(p, "function_call") and p.function_call
        ]
        if not tool_calls:
            return history  # model issued its final text reply

        # Execute all tool calls and collect responses
        response_parts = []
        for part in tool_calls:
            fc = part.function_call
            args = dict(fc.args)
            _print_call(fc.name, args)
            result = _dispatch(fc.name, args)
            _print_result(result)
            response_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        id=getattr(fc, "id", None) or fc.name,
                        name=fc.name,
                        response={"result": result},
                    )
                )
            )
        _console.print()

        # Feed tool results back as a user-role turn (Gemini convention)
        history.append(types.Content(role="user", parts=response_parts))


# ---------------------------------------------------------------------------
# CLI chat loop
# ---------------------------------------------------------------------------

def chat_loop(model: str = CU_MODEL):
    client, model, config = build_agent(model)
    history: list = []

    _console.print()
    _console.print(Panel(
        f"[bold white]Rote[/] Desktop Assistant\n[dim]{model}[/]  ·  [dim]quit to exit[/]",
        box=box.ROUNDED,
        border_style="cyan",
        padding=(0, 2),
    ))
    _console.print()

    while True:
        try:
            user_input = _console.input("[bold cyan]❯[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            _console.print("\n[dim]Bye.[/]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            _console.print("[dim]Bye.[/]")
            break

        _console.print()
        history.append(
            types.Content(role="user", parts=[types.Part(text=user_input)])
        )
        history = run_turn(client, model, config, history)

        # Print the model's final text reply
        last = history[-1]
        if hasattr(last, "parts"):
            text = "".join(p.text for p in last.parts if hasattr(p, "text") and p.text)
            if text:
                _console.print(Panel(
                    text,
                    title="[bold]Rote[/]",
                    border_style="green",
                    box=box.ROUNDED,
                    padding=(0, 1),
                ))
        _console.print()
