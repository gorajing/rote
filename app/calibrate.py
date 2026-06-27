"""Coordinate-calibration harness for the CU loop.

Does Gemini's normalized (0-999) coordinate + our denormalization actually land clicks
on small, adjacent targets? Wikipedia/Google never tested this; the dense AcmeBilling UI
will. Run this before trusting click accuracy on the hero workflow.

  python -m app.calibrate

Drives the agent to click specific grid buttons, then reads back (from the page's own
click log) which buttons were actually hit — an end-to-end measure of click accuracy.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

from .config import VIEWPORT
from .schemas import Task
from .cu_runner import run_task

PAGE = (Path(__file__).parent / "calibration.html").resolve().as_uri()
TARGETS = ["R1C1", "R2C3", "R3C4"]   # top-left, center, bottom-right — accuracy across the screen


def main(headless: bool = False):
    intent = ("Click these buttons in this exact order, exactly one click each: "
              + ", ".join(TARGETS) + ". Do not click any other button.")
    task = Task(id="calib", site="calibration", intent=intent,
                params={}, checker="", family="calibration")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_context(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]}).new_page()
        page.goto(PAGE, wait_until="domcontentloaded")
        run_task(task, page)
        clicked = page.evaluate("() => window.__clicks")
        browser.close()

    hits = [t for t in TARGETS if t in clicked]
    print("\n=== CALIBRATION RESULT ===")
    print("targets:", TARGETS)
    print("clicked:", clicked)
    print(f"hit {len(hits)}/{len(TARGETS)} intended targets", end="  ")
    if len(hits) == len(TARGETS) and len(clicked) == len(TARGETS):
        print("✓ clean — coord accuracy is good for dense UI")
    elif len(hits) == len(TARGETS):
        print("✓ targets hit, but extra clicks (model over-clicked, not a coord issue)")
    else:
        print("⚠ MISSES — coords are landing off small targets; investigate denorm/viewport")


if __name__ == "__main__":
    main()
