"""Run a macro replay with the Dynamic-Island HUD narrating it live.

  python -m app.desktop_hud --skill create_word_file

The HUD (main thread) animates a spinner + step + progress bar by the notch, while the replay
runs on a worker thread — so even multi-second app loads look like active progress, not a freeze.
"""
import argparse
import json

from .desktop_cu import probe
from .local_skill_registry import LocalSkillRegistry
from .notch import NotchIsland
from .skill_repair import RepairService
from .verified_replay import replay_verified


def main():
    ap = argparse.ArgumentParser()
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--skill", help="registered macro name, e.g. create_word_file")
    source.add_argument("--replay", help="legacy path to a macro JSON file")
    ap.add_argument("--repair", action="store_true", help="repair one failed transition and validate it")
    ap.add_argument("--events", action="store_true",
                    help="emit @@EV <json> lines on stdout for an external narrator (e.g. the voice agent)")
    a = ap.parse_args()

    if not probe():
        raise SystemExit("Fix Screen Recording / Accessibility permissions first.")

    registry = LocalSkillRegistry()
    if a.skill:
        macro = registry.load_skill(a.skill)
    else:
        with open(a.replay, encoding="utf-8") as macro_file:
            macro = json.load(macro_file)
    hud = NotchIsland()

    def work():
        def event(kind, payload):
            if a.events:                                   # structured stream for the voice narrator
                rec = {"kind": kind}
                if kind == "step":
                    st = payload["step"]
                    rec.update(index=payload["index"], total=payload["total"],
                               op=st.get("op"), app=st.get("app"), keys=st.get("keys"),
                               skill=st.get("skill"), why=st.get("why", st.get("op")))
                print("@@EV " + json.dumps(rec, default=str), flush=True)
            if kind == "step":
                step = payload["step"]
                hud.step(payload["index"], payload["total"], step.get("why", step["op"]))
            elif kind == "repairing":
                hud.status("Repairing failed step…")
            elif kind == "validating":
                hud.status("Validating candidate…")
            elif kind == "promoted":
                hud.status("Skill promoted ✓")
            elif kind == "rejected":
                hud.status("Candidate rejected")

        result = replay_verified(
            macro, allow_repair=a.repair, registry=registry,
            repair_service=RepairService(registry) if a.repair else None,
            on_event=event,
        )
        hud.finish("Done ✓" if result["success"] else "Verification failed")

    hud.run(work)


if __name__ == "__main__":
    main()
