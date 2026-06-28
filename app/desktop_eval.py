"""Skills-off ablation for the DESKTOP loop — Rote's core proof, run live.

Same task, two arms:
  COLD       — no recipe; Gemini figures it out from scratch
  WITH SKILL — a verified markdown recipe injected as an intent-sequence

Prints steps / wall-clock / tokens side by side so you can SEE which is better.
State is reset between arms (quit Word, delete the output file) for a fair comparison.

  python -m app.desktop_eval
"""
import os
import time
import json
import subprocess
from pathlib import Path

from .desktop_cu import run, probe

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "database" / "skills" / "create_word_file.md"
OUT = Path.home() / "Desktop" / "gemini.docx"
LOCK = Path.home() / "Desktop" / "~$gemini.docx"

TEXT = "Hello from Gemini Computer Use."
INTENT = (f"Create a new Microsoft Word document, type the sentence '{TEXT}' into it, then "
          "save it to the Desktop with the filename 'gemini'. Use Command+S to save; if a "
          "location picker appears choose 'On My Mac' and the Desktop folder.")


def reset():
    """Quit Word and remove the output file so each arm starts from the same clean state."""
    subprocess.run(["osascript", "-e", 'tell application "Microsoft Word" to quit saving no'],
                   check=False, capture_output=True)
    time.sleep(2)
    for p in (OUT, LOCK):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    time.sleep(1)


def verify() -> bool:
    if not OUT.exists():
        return False
    try:
        import zipfile, re
        xml = zipfile.ZipFile(OUT).read("word/document.xml").decode("utf-8", "ignore")
        return TEXT in " ".join(re.findall(r"<w:t[^>]*>([^<]+)</w:t>", xml))
    except Exception:
        return False


def arm(label, skill_md):
    print(f"\n{'='*60}\nARM: {label}\n{'='*60}")
    reset()
    m = run(INTENT, skill_md=skill_md)
    m["label"] = label
    m["file_ok"] = verify()
    return m


if __name__ == "__main__":
    if not probe():
        raise SystemExit("Fix Screen Recording / Accessibility permissions first.")

    if not SKILL.exists():
        raise SystemExit(
            f"Missing skill recipe: {SKILL}. The old tracked seed catalog was intentionally "
            "deleted after the DB runtime pivot; record or provide a fresh recipe first."
        )
    skill_md = SKILL.read_text()
    cold = arm("COLD (no skill)", None)
    withskill = arm("WITH SKILL", skill_md)

    def row(m):
        return (f"  {m['label']:<18} steps={m['steps']:<3} time={m['elapsed_s']:<6}s "
                f"tokens={m['tokens']:<8} file_ok={m['file_ok']}")

    print(f"\n\n{'#'*60}\n# RESULT — skills-off ablation\n{'#'*60}")
    print(row(cold))
    print(row(withskill))
    if cold["steps"] and withskill["steps"]:
        def delta(a, b):  # change of WITH-SKILL relative to COLD; +% = skill used MORE (worse)
            return 100 * (b - a) / a if a else 0
        ds, dt, dk = (delta(cold["steps"], withskill["steps"]),
                      delta(cold["elapsed_s"], withskill["elapsed_s"]),
                      delta(cold["tokens"], withskill["tokens"]))
        print(f"\n  WITH SKILL vs COLD  (negative = skill is better):")
        print(f"    steps {ds:+.0f}%   time {dt:+.0f}%   tokens {dk:+.0f}%")
    Path(REPO / "traces").mkdir(exist_ok=True)
    (REPO / "traces" / "ablation_desktop.json").write_text(json.dumps([cold, withskill], indent=2))
    print("\n  saved -> traces/ablation_desktop.json")
