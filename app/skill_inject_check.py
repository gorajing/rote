"""A<->B integration test: does injecting a compiled Skill actually steer the agent?

Runs the SAME task twice — COLD (no skill) vs WARM (skill injected) — and reports the
delta in steps + success. This is the empirical answer to "do learned skills help, or is
CU already good enough alone?" — run it against C's AcmeBilling app at the H6 milestone.

Uses a stub compiler until B's compile_skill lands; swap one import when Riccardo pushes.

  python -m app.skill_inject_check --url http://localhost:8800/billing
"""
import argparse
from dataclasses import replace
from pathlib import Path

from .schemas import Skill, Task
from .trace import load_trajectory
from .runner import run_episode
from .cu_runner import _skill_hint

FIXTURE = str(Path(__file__).resolve().parent.parent / "examples" / "sample_trajectory.json")


def stub_compile_skill(traj) -> Skill:
    """Stand-in for B's compile_skill: derive a Skill from a trajectory's intents.
    Replace with `from .skill_compiler import compile_skill` once Riccardo's lands."""
    steps = [{"intent": s.intent, "action": s.action, "target_desc": s.intent}
             for s in traj.steps if s.action != "wait"]
    return Skill(
        name="dispute_unpaid_invoice", site="billing",
        goal_template="Mark the unpaid invoice from {customer} as disputed, add note {note}, export receipt",
        params=[{"name": "customer", "type": "str", "required": True},
                {"name": "note", "type": "str", "required": True}],
        preconditions={"url_pattern": "/billing"},
        steps=steps,
        success_checks=["invoice.status == disputed"],
    )


def compare(task: Task, url: str, skill: Skill, checker=None):
    """COLD (no skill) vs WARM (skill injected). Positive Δ steps = the skill helped."""
    cold, _ = run_episode(replace(task, id=task.id + "-cold"), url, checker=checker, skills=None)
    warm, _ = run_episode(replace(task, id=task.id + "-warm"), url, checker=checker, skills=[skill])
    print("\n=== A<->B SKILL-INJECTION CHECK ===")
    print(f"COLD (no skill): {cold.n_steps} steps, success={cold.success}")
    print(f"WARM (skill):    {warm.n_steps} steps, success={warm.success}, used_skill={warm.used_skill}")
    print(f"Δ steps (cold - warm): {cold.n_steps - warm.n_steps}  (positive = skill helped)")
    return cold, warm


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8800/billing")
    args = ap.parse_args()
    skill = stub_compile_skill(load_trajectory(FIXTURE))
    print(f"compiled '{skill.name}' ({len(skill.steps)} steps) from the fixture")
    task = Task(id="inject", site="billing",
                intent=("Find the unpaid invoice from Acme Corp, mark it disputed, "
                        "add the note 'duplicate charge', then export the receipt."),
                params={"customer": "Acme Corp", "note": "duplicate charge"},
                checker="dispute_workflow", family="invoice_action")
    compare(task, args.url, skill)
