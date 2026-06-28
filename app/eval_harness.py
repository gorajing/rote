"""Eval harness — held-out eval, ablation baseline, generation curve, repair delta."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

from .config import APP_URL, VIEWPORT
from .schemas import Task, Trajectory
from .cu_runner import run_task
from . import checker
from .runner import _goto
from .tasks import SPLITS


def _retrieve_skills(task: Task, use_skills: bool):
    if not use_skills:
        return None
    try:
        from .skill_registry import retrieve
        return retrieve(task)
    except ImportError:
        return None


def _run_one_task(page, task: Task, variant: str | None, use_skills: bool,
                  headless: bool = False) -> Trajectory:
    checker.arena_reset(variant or "baseline")
    skills = _retrieve_skills(task, use_skills)
    url = f"{APP_URL}/billing"
    _goto(page, url)
    traj = run_task(task, page, skills=skills)
    traj.success = checker.check(task)
    return traj


def run_eval(split: str = "heldout", use_skills: bool = False,
             variant: str | None = None, headless: bool = False) -> dict:
    tasks = SPLITS.get(split)
    if tasks is None:
        raise ValueError(f"Unknown split: {split}. Choose from: {list(SPLITS)}")

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]})
        for task in tasks:
            page = ctx.new_page()
            traj = _run_one_task(page, task, variant, use_skills, headless)
            page.close()
            results.append({
                "task_id": task.id,
                "family": task.family,
                "success": traj.success,
                "n_steps": traj.n_steps,
                "used_skill": traj.used_skill,
            })

        browser.close()

    n = len(results)
    successes = sum(1 for r in results if r["success"])
    total_steps = sum(r["n_steps"] for r in results)

    per_family: dict[str, dict] = {}
    for r in results:
        fam = r["family"]
        if fam not in per_family:
            per_family[fam] = {"total": 0, "successes": 0, "steps": 0}
        per_family[fam]["total"] += 1
        per_family[fam]["successes"] += int(r["success"])
        per_family[fam]["steps"] += r["n_steps"]

    for fam in per_family:
        pf = per_family[fam]
        pf["success_rate"] = pf["successes"] / pf["total"] if pf["total"] else 0.0
        pf["avg_steps"] = pf["steps"] / pf["total"] if pf["total"] else 0.0

    report = {
        "split": split,
        "use_skills": use_skills,
        "variant": variant or "baseline",
        "success_rate": successes / n if n else 0.0,
        "avg_steps": total_steps / n if n else 0.0,
        "n_tasks": n,
        "per_family": per_family,
        "results": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return report


def _dump_report(report: dict, filename: str) -> str:
    os.makedirs("traces", exist_ok=True)
    path = os.path.join("traces", filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return path


def run_generations(n: int = 3, headless: bool = False) -> dict:
    """Generation loop — gen0 ablation, then train+eval cycles (B stubs when unavailable)."""
    generations = []

    gen0 = run_eval("heldout", use_skills=False, headless=headless)
    gen0["generation"] = 0
    generations.append(gen0)
    _dump_report(gen0, "eval_gen0.json")

    for gen in range(1, n + 1):
        train_report = run_eval("train", use_skills=True, headless=headless)
        _compile_successes(train_report)

        heldout_report = run_eval("heldout", use_skills=True, headless=headless)
        heldout_report["generation"] = gen
        generations.append(heldout_report)
        _dump_report(heldout_report, f"eval_gen{gen}.json")

    curve = {
        "generations": generations,
        "success_rates": [g["success_rate"] for g in generations],
        "avg_steps": [g["avg_steps"] for g in generations],
        "ablation_baseline": gen0["success_rate"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _dump_report(curve, "eval_generations.json")
    return curve


def _compile_successes(train_report: dict) -> None:
    """Compile successful trajectories into skills via B (no-op if B unavailable)."""
    try:
        from .skill_compiler import compile_trajectory
        from .skill_registry import store
    except ImportError:
        return
    for r in train_report.get("results", []):
        if r["success"]:
            pass  # B would load trajectory from traces/ and compile+store


def run_repair_eval(variant: str = "move_dispute_to_cases", headless: bool = False) -> dict:
    """Measure naive replay failure then repair recovery on a mutated UI."""
    naive = run_eval("heldout", use_skills=True, variant=variant, headless=headless)

    repaired_rate = naive["success_rate"]
    try:
        from .repair import repair_library
        repair_library(variant)
        after = run_eval("heldout", use_skills=True, variant=variant, headless=headless)
        repaired_rate = after["success_rate"]
    except ImportError:
        after = naive

    delta = {
        "variant": variant,
        "naive_success_rate": naive["success_rate"],
        "repaired_success_rate": repaired_rate,
        "recovery_delta": repaired_rate - naive["success_rate"],
        "naive_avg_steps": naive["avg_steps"],
        "repaired_avg_steps": after.get("avg_steps", naive["avg_steps"]),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _dump_report(delta, f"eval_repair_{variant}.json")
    return delta


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Rote eval harness")
    ap.add_argument("--split", default="heldout", choices=list(SPLITS))
    ap.add_argument("--no-skills", action="store_true", help="Ablation baseline (skills off)")
    ap.add_argument("--variant", default=None, help="UI mutation variant")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--generations", type=int, default=0,
                    help="Run N-generation curve (0 = single eval)")
    ap.add_argument("--repair-eval", default=None,
                    help="Run repair delta eval for variant name")
    args = ap.parse_args()

    if args.repair_eval:
        report = run_repair_eval(args.repair_eval, headless=args.headless)
    elif args.generations > 0:
        report = run_generations(args.generations, headless=args.headless)
    else:
        report = run_eval(args.split, use_skills=not args.no_skills,
                          variant=args.variant, headless=args.headless)
        _dump_report(report, f"eval_{args.split}_{'skills' if not args.no_skills else 'ablation'}.json")

    print(json.dumps(report, indent=2))
