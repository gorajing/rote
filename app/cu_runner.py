"""The Gemini 3.5 Computer Use loop → Trajectory. CU is the protagonist.
Interactions API (now GA): screenshot in -> function_call out -> execute -> screenshot back,
chained by previous_interaction_id. Ref: ai.google.dev/gemini-api/docs/computer-use.

A learned skill is injected as its INTENT-SEQUENCE (not coordinates): the model re-grounds
the pixels live from the current screenshot. That is what makes replay self-healing, not RPA."""
import json
from google import genai

from .config import (CU_MODEL, LEGACY_CU_MODEL, USE_LEGACY_CU,
                     MAX_TURNS, STUCK_AFTER)
from .schemas import Task, Trajectory
from .executor import execute_action
from .trace import screenshot_b64, save_screenshot, state_hash, record_step

_client = genai.Client()
_MODEL = LEGACY_CU_MODEL if USE_LEGACY_CU else CU_MODEL
_TOOL = [{"type": "computer_use", "environment": "browser", "enable_prompt_injection_detection": True}]


def _skill_hint(skills) -> str:
    """Render retrieved skills as intent-sequences the model should prefer (re-grounded visually)."""
    if not skills:
        return ""
    lines = ["You have reliable recipes for this site. If one matches the goal, follow its steps, "
             "re-locating each target on the CURRENT screen:"]
    for s in skills:
        path = " -> ".join(step.get("target_desc", step.get("intent", "?")) for step in s.steps)
        lines.append(f"- {s.name}: {path}")
    return "\n".join(lines) + "\n\nGOAL: "


def run_task(task: Task, page, skills=None, out_dir="traces") -> Trajectory:
    """Drive Gemini CU through one task on `page`; return the annotated Trajectory.
    Success is NOT decided here — the deterministic checker fills traj.success afterward."""
    traj = Trajectory(task_id=task.id, used_skill=(skills[0].name if skills else None))
    prompt = _skill_hint(skills) + task.intent

    save_screenshot(page, out_dir, task.id, 0)
    interaction = _client.interactions.create(
        model=_MODEL,
        input=[
            {"type": "text", "text": prompt},
            {"type": "image", "data": screenshot_b64(page), "mime_type": "image/png"},
        ],
        tools=_TOOL,
    )

    recent = []
    for turn in range(1, MAX_TURNS + 1):
        calls = [s for s in interaction.steps if s.type == "function_call"]
        if not calls:                                   # model is done
            traj.final_text = " ".join(
                c.text for s in interaction.steps if s.type == "model_output"
                for c in s.content if c.type == "text"
            )
            break

        responses = []
        for call in calls:
            args = dict(call.arguments)
            shot = save_screenshot(page, out_dir, task.id, turn)
            record_step(traj, turn, call.name, args, page, shot)   # capture intent + action + coords
            result = execute_action(page, call.name, args)
            if "safety_decision" in args:               # sandboxed controlled app: ack and continue
                result["safety_acknowledgement"] = True
            responses.append({
                "type": "function_result",
                "name": call.name,
                "call_id": call.id,
                "result": [
                    {"type": "text", "text": json.dumps({"url": page.url, **result})},
                    {"type": "image", "data": screenshot_b64(page), "mime_type": "image/png"},
                ],
            })

        # circuit breaker: if screen+url is identical STUCK_AFTER turns in a row, bail (and that
        # failure trace is exactly what the repair step learns from).
        recent.append(state_hash(page))
        if len(recent) >= STUCK_AFTER and len(set(recent[-STUCK_AFTER:])) == 1:
            traj.final_text = "ABORTED: stuck (no progress)"
            break

        interaction = _client.interactions.create(
            model=_MODEL,
            previous_interaction_id=interaction.id,
            input=responses,
            tools=_TOOL,
        )

    return traj
