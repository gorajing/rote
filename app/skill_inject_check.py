"""A<->B integration test: does injecting a compiled Skill actually steer the agent?

Runs the SAME task twice — COLD (no skill) vs WARM (skill injected) — and reports the
delta in steps + success. Gate: cold success < ~70%, cold steps ~12-18 vs warm ~6.

  python -m app.skill_inject_check --url http://localhost:8800/billing
"""
import argparse
from dataclasses import replace

from .config import APP_URL
from .schemas import Skill, Task
from .trace import load_trajectory
from .runner import run_episode
from .tasks import HERO
from . import checker

FIXTURE = __import__("pathlib").Path(__file__).resolve().parent.parent / "examples" / "sample_trajectory.json"


def stub_compile_skill(traj) -> Skill:
    steps = [{"intent": s.intent, "action": s.action, "target_desc": s.intent}
             for s in traj.steps if s.action != "wait"]
    return Skill(
        name="dispute_unpaid_invoice", site="billing",
        goal_template="Dispute unpaid invoice from {customer}, note {note}, export",
        params=[{"name": "customer", "type": "str", "required": True},
                {"name": "note", "type": "str", "required": True}],
        preconditions={"url_pattern": "/billing"},
        steps=steps,
        success_checks=["invoice.status == disputed"],
    )


def compare(task: Task, url: str, skill: Skill):
    score = checker.check if url.startswith(APP_URL) else None
    cold, _ = run_episode(replace(task, id=task.id + "-cold"), url, checker=score, skills=None)
    warm, _ = run_episode(replace(task, id=task.id + "-warm"), url, checker=score, skills=[skill])
    print("\n=== A<->B SKILL-INJECTION CHECK ===")
    print(f"COLD (no skill): {cold.n_steps} steps, success={cold.success}")
    print(f"WARM (skill):    {warm.n_steps} steps, success={warm.success}, used_skill={warm.used_skill}")
    delta = cold.n_steps - warm.n_steps
    print(f"Δ steps (cold - warm): {delta}  (positive = skill helped)")
    if cold.success and cold.n_steps <= 8:
        print("⚠ Arena may still be too easy — target cold <70% success, ~12-18 steps")
    return cold, warm


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=f"{APP_URL}/billing")
    args = ap.parse_args()
    skill = stub_compile_skill(load_trajectory(str(FIXTURE)))
    print(f"compiled '{skill.name}' ({len(skill.steps)} steps) from the fixture")
    compare(HERO, args.url, skill)
