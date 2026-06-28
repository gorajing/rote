"""Speed proof: cold computer-use vs. compiled-skill REPLAY — Rote's real performance thesis.

  COLD    — Gemini drives the desktop from scratch (screenshot -> infer -> act, every step)
  REPLAY  — the compiled keyboard macro runs locally: NO screenshots, NO model calls.
            If the post-replay success check fails, it SELF-HEALS by handing off to the model.

Prints the latency breakdown (how much of COLD is model inference) and the speedup.

  python -m app.desktop_speed
"""
import json
import time
import subprocess
from pathlib import Path

from .desktop_cu import run, replay, probe

REPO = Path(__file__).resolve().parent.parent
MACRO = REPO / "database" / "skills" / "create_word_file.macro.json"
OUT = Path.home() / "Desktop" / "gemini.docx"
LOCK = Path.home() / "Desktop" / "~$gemini.docx"
TEXT = "Hello from Gemini Computer Use."
INTENT = (f"Create a new Microsoft Word document, type the sentence '{TEXT}' into it, then "
          "save it to the Desktop with the filename 'gemini'. Use Command+S to save.")


def reset():
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


if __name__ == "__main__":
    if not probe():
        raise SystemExit("Fix Screen Recording / Accessibility permissions first.")

    print(f"\n{'='*60}\nARM 1: COLD computer-use (model every step)\n{'='*60}")
    reset()
    cold = run(INTENT)
    cold["file_ok"] = verify()

    print(f"\n{'='*60}\nARM 2: REPLAY compiled macro (no model)\n{'='*60}")
    reset()
    macro = json.loads(MACRO.read_text())
    rep = replay(macro)
    rep["file_ok"] = verify()
    if not rep["file_ok"]:                     # SELF-HEAL: macro drifted -> let the model finish
        print("\n  replay success-check FAILED -> self-healing with the model...")
        heal = run(INTENT)
        rep["heal_steps"] = heal["steps"]
        rep["heal_s"] = heal["elapsed_s"]
        rep["elapsed_s"] = round(rep["elapsed_s"] + heal["elapsed_s"], 1)
        rep["file_ok"] = verify()

    print(f"\n\n{'#'*60}\n# RESULT — cold CU vs compiled replay\n{'#'*60}")
    print(f"  COLD    steps={cold['steps']:<3} time={cold['elapsed_s']:<6}s  "
          f"(model {cold['model_pct']}% = {cold['model_s']}s)  tokens={cold['tokens']:<7} file_ok={cold['file_ok']}")
    print(f"  REPLAY  steps={rep['steps']:<3} time={rep['elapsed_s']:<6}s  "
          f"(model 0%)            tokens={rep['tokens']:<7} file_ok={rep['file_ok']}")
    if rep["elapsed_s"]:
        print(f"\n  SPEEDUP: {cold['elapsed_s'] / rep['elapsed_s']:.1f}x faster   "
              f"tokens saved: {cold['tokens'] - rep['tokens']:,}")
    Path(REPO / "traces").mkdir(exist_ok=True)
    (REPO / "traces" / "speed_desktop.json").write_text(json.dumps([cold, rep], indent=2))
    print("\n  saved -> traces/speed_desktop.json")
