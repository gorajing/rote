"""CLI for verified desktop replay, history, and one-shot localized self-repair."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .local_skill_registry import LocalSkillRegistry
from .skill_repair import RepairService, reset_stale_word
from .verified_replay import replay_verified


def _params(items: list[str]) -> dict:
    result = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"parameter must be name=value: {item}")
        key, value = item.split("=", 1)
        result[key] = value
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Rote step-level self-improvement")
    parser.add_argument("command", choices=("replay", "repair", "demo", "history"))
    parser.add_argument("skill")
    parser.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--metrics", default=None, help="optional result JSON path")
    args = parser.parse_args()

    registry = LocalSkillRegistry()
    if args.command == "history":
        print(json.dumps(registry.get_history(args.skill), indent=2))
        return

    skill = registry.load_skill(args.skill)
    params = {**skill.get("params", {}), **_params(args.param)}

    def event(kind, payload):
        if kind == "step":
            step = payload["step"]
            print(f"[{payload['index']}/{payload['total']}] {step['id']}: {step.get('why', step['op'])}")
        elif kind in ("repairing", "validating", "promoted", "rejected"):
            print(f"[{kind.upper()}]")

    if args.command == "demo":
        reset_stale_word(params)
        before = replay_verified(skill, params, backend=None, registry=registry, on_event=event)
        if before["success"]:
            report = {"before": before, "repair": None, "after": before,
                      "note": "The active shared subskill is already repaired."}
        else:
            service = RepairService(registry, reset=reset_stale_word)
            repaired = service.repair_and_validate(skill, params, before, backend=None, on_event=event)
            repaired["repair_calls"] = 1
            repaired["model_calls"] = repaired.get("model_calls", 0) + 1
            reset_stale_word(params)
            after = replay_verified(registry.load_skill(args.skill), params, registry=registry, on_event=event)
            report = {"before": before, "repair": repaired, "after": after}
        serializable = json.loads(json.dumps(report, default=str))
        print(json.dumps(serializable, indent=2))
        if args.metrics:
            path = Path(args.metrics)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
        raise SystemExit(0 if report["after"]["success"] else 1)

    repair_service = RepairService(registry) if args.command == "repair" else None
    result = replay_verified(
        skill, params, allow_repair=args.command == "repair",
        repair_service=repair_service, registry=registry, on_event=event,
    )
    registry.record_run(skill, result)
    serializable = {key: value for key, value in result.items() if key != "failure"}
    print(json.dumps(serializable, indent=2, default=str))
    if args.metrics:
        path = Path(args.metrics)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(serializable, indent=2, default=str), encoding="utf-8")
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
