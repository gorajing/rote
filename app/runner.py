"""Runnable entry for the CU engine: owns the Playwright lifecycle and drives one Task
through cu_runner. This is the smoke test that proves the loop works end-to-end.

  pip install -r requirements.txt && playwright install chromium
  export GEMINI_API_KEY=...

  # validate the loop against a public page first (no controlled app needed yet):
  python -m app.runner --url https://www.google.com --intent "Search for 'Gemini API'."

  # once Owner C's controlled app is up, drive the real hero workflow (the default):
  python -m app.runner
"""
import argparse
from playwright.sync_api import sync_playwright

from .config import VIEWPORT, APP_URL, TRACES_DIR
from .schemas import Task
from .cu_runner import run_task
from .trace import save_trajectory


def _goto(page, url, attempts: int = 3):
    """Robust navigation: wait for DOM (not full 'load'), and retry the transient
    'interrupted by another navigation to about:blank' race that can hit headful goto."""
    for i in range(attempts):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            return
        except Exception as e:
            if "interrupted by another navigation" in str(e) and i < attempts - 1:
                continue
            raise


def drive(task: Task, start_url: str, skills=None, headless: bool = False):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]})
        page = ctx.new_page()
        _goto(page, start_url)
        traj = run_task(task, page, skills=skills)
        browser.close()
    return traj


def run_episode(task: Task, start_url: str, checker=None, skills=None,
                headless: bool = False, out_dir: str = TRACES_DIR):
    """One full episode: drive the task, optionally score it with C's deterministic
    checker, and persist the trajectory. Returns (trajectory, saved_path).

    `checker` is any callable check(task) -> bool — pass C's check_task once it lands;
    until then the episode still runs and the trajectory is saved (success stays None).
    This is the loop H6 and the eval harness both run on."""
    traj = drive(task, start_url, skills=skills, headless=headless)
    if checker is not None:
        traj.success = checker(task)
    path = save_trajectory(traj, out_dir)
    return traj, path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=f"{APP_URL}/billing")
    ap.add_argument("--intent",
                    default="Find the unpaid invoice from Acme Corp, mark it disputed, "
                            "add the note 'duplicate charge', then export the receipt.")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    task = Task(id="smoke-1", site="billing", intent=args.intent,
                params={"customer": "Acme Corp", "note": "duplicate charge"},
                checker="dispute_workflow", family="invoice_action")

    traj, path = run_episode(task, args.url, headless=args.headless)

    print(f"\n=== Trajectory: {traj.n_steps} steps, used_skill={traj.used_skill} ===")
    for s in traj.steps:
        print(f"  [{s.turn:>2}] {s.action:<14} @ {str(s.coords):<12} — {s.intent}")
    print(f"final: {traj.final_text}")
    print(f"saved: {path}   (B can compile a skill from this; D can replay it)")
