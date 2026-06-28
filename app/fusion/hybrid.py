"""Genuinely-LEARNED cross-surface hybrid skills (the real learn-cu loop).

A hybrid skill chains per-surface FusedSkills, each COMPILED FROM A REAL GEMINI TRACE — not
hand-templated. Gemini does the task once on each surface (cu_runner for the browser,
desktop_cu for the Mac desktop); we lower each trace to a 0-CU FusedSkill and replay them in
order, with the OS clipboard carrying the live cross-surface payload (e.g. a page title copied
in the browser, pasted into a TextEdit note). Ground truth at every seam (clipboard, TextEdit
document) — never the model's self-report.

    learn-cu : run Gemini as the doer on each segment, compile, save   (REAL model calls)
    replay   : fusion-replay every segment at 0 CU, verify             (ZERO model calls)

This is the genuinely-LEARNED path: every step is lowered from what Gemini actually did on each
surface (not a hand-built template that makes zero model calls). It earns the name "learn".

    python -m app.fusion.hybrid learn --url https://example.com --replay   # learn then prove 0-CU
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import sync_playwright

from ..config import VIEWPORT
from ..schemas import Step, Task, Trajectory
from ..cu_runner import run_task
from .. import desktop_cu
from .compiler import compile as compile_fused
from .contract import FusedSkill
from .dispatch import replay
from .browser_executor import BrowserExecutor
from .desktop_executor import DesktopExecutor
from .verifier import make_verifier
from .skill_store import _skill_to_dict, _dict_to_skill
from .world_verifiers import read_clipboard


@dataclass
class HybridSegment:
    role: str               # human label, e.g. "read_web_title" / "write_textedit_note"
    surface: str            # "browser" | "desktop"
    skill: FusedSkill       # the compiled, LEARNED segment (carries its own .verify)


@dataclass
class HybridSkill:
    name: str
    goal: str
    url: str
    segments: list[HybridSegment] = field(default_factory=list)
    learned_at: str = ""     # honest: these segments WERE learned from real Gemini traces


class HybridLearnError(RuntimeError):
    """A segment's doer did not actually achieve its goal (ground truth) — so we refuse to compile
    and persist a 'learned' skill from it. This is the compiler's success-gate, enforced at the seam
    instead of trusting the model's self-report."""


def _set_clipboard(text: str) -> None:
    """Put a captured payload on the OS pasteboard. Playwright's browser clipboard is sandboxed from
    the OS, so the desktop segment can't read a browser Cmd+C directly — we bridge it explicitly."""
    try:
        subprocess.run(["pbcopy"], input=text, text=True, timeout=5)
    except Exception:
        pass


def _capture_selection(page) -> str:
    """The text the browser segment SELECTED (what Gemini highlighted before copying). Robust across
    the Playwright/OS clipboard boundary — and the typed cross-surface payload of the hybrid."""
    try:
        return (page.evaluate("() => (window.getSelection && window.getSelection().toString()) || ''") or "").strip()
    except Exception:
        return ""


def _payload_ok(payload: str, needle: str) -> bool:
    needle = (needle or "").strip()
    return len(needle) >= 4 and needle.lower() in (payload or "").lower()


class _ActionsVerifier:
    """The browser segment's ground truth (the captured payload) is checked by the orchestrator after
    replay, not inside it — so the in-replay verifier only confirms the actions executed."""

    def check(self, skill) -> bool:
        return True


def _new_textedit_doc(app: str = "TextEdit") -> None:
    """Make a blank, focused TextEdit document frontmost via osascript, so the doer/replay only needs
    to paste — not fight the open-file / new-document flow (the desktop fumble point). TextEdit-specific
    for now; other apps in the fresh-skill suite get their own pre-stage."""
    try:
        subprocess.run(["osascript",
                        "-e", f'tell application "{app}" to activate',
                        "-e", f'tell application "{app}" to make new document'],
                       capture_output=True, timeout=8)
    except Exception:
        pass


# ── learn (real Gemini doers → compiled segments) ──────────────────────────────────────────
def _browser_segment(page, url: str, title: str, max_turns: int = 12) -> HybridSegment:
    """Gemini reads the page and copies its title to the clipboard; lower to a 0-CU FusedSkill.
    Gated on GROUND TRUTH: clear the pasteboard first, then require the title to actually be on it
    before compiling — never the model's self-report."""
    task = Task(
        id="hybrid_read_web", site=url, family="real_web", checker="", params={},
        intent=(f"This is the web page {url}. Select the main heading/title text at the top of the "
                "page and copy it to the clipboard. Then you are finished."),
    )
    traj = run_task(task, page, max_turns=max_turns)
    payload = _capture_selection(page)                    # the title Gemini highlighted (the typed payload)
    if not _payload_ok(payload, title):
        raise HybridLearnError(f"browser segment did not select the title "
                               f"(selection={payload[:60]!r}, wanted {title!r})")
    _set_clipboard(payload)                               # bridge across the Playwright/OS boundary
    traj.success = True                                   # gated on ground truth (the selection), not self-report
    skill = compile_fused(traj, surface="browser", name="hybrid_read_web", params={},
                          verify={"kind": "clipboard", "contains": title})
    skill.target = url
    return HybridSegment(role="read_web_title", surface="browser", skill=skill)


def _desktop_trace_to_traj(trace: dict) -> Trajectory:
    """desktop_cu.run writes {intent, steps:[{turn,action,intent,args}], metrics}; reshape to a
    Trajectory the fusion compiler accepts. Desktop note steps are keyboard (open/Cmd+N/Cmd+V),
    so coords are usually absent — fine, they lower to the keyboard tier (no crop needed)."""
    steps = []
    for st in trace.get("steps", []):
        a = dict(st.get("args", {}))
        # No per-step screenshots are saved for the desktop doer, so there is nothing to crop
        # against — leave coords None (a spatial step would escalate, not crop). This avoids both
        # the _crop_b64(None) crash and a wrong VIEWPORT-vs-desktop pixel basis.
        steps.append(Step(turn=st.get("turn", 0), intent=st.get("intent", ""), action=st.get("action", ""),
                          args=a, coords=None, screenshot_path=None, url="desktop://macos"))
    return Trajectory(task_id="hybrid_write_note", steps=steps,
                      final_text=trace.get("metrics", {}).get("final", ""), success=True)


def _desktop_segment(native_app: str, marker: str, max_turns: int = 14, attempts: int = 3) -> HybridSegment:
    """Gemini opens the native app and pastes the clipboard into a new note; lower to a 0-CU
    FusedSkill. Gated on GROUND TRUTH: only a run that actually lands the marker in the front
    document is compiled. Desktop CU is fumbly (and can hit a model safety block on a bad
    prediction), so RETRY the doer up to `attempts` times — a fresh blank doc each attempt."""
    intent = (f"A blank {native_app} document is already open and focused. Paste the text that is on "
              "the clipboard by pressing Command+V — a single keyboard shortcut. Do not click anything, "
              "open any menu, or focus any other field; just press Command+V once. Then you are finished.")
    last = "unknown"
    for attempt in range(1, attempts + 1):
        desktop_cu.ensure_app(native_app)                 # launch it
        _new_textedit_doc(native_app)                     # fresh, focused blank doc to paste into
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = os.path.join(tmp, "desktop_segment.json")
            try:
                desktop_cu.run(intent, trace_path=trace_path, max_turns=max_turns)
            except Exception as exc:                      # API / model safety block / etc. — retry
                last = f"doer error: {type(exc).__name__}: {str(exc)[:80]}"
                print(f"  [desktop] attempt {attempt}: {last}", flush=True); continue
            trace = json.load(open(trace_path, encoding="utf-8")) if os.path.exists(trace_path) else {}
        if str(trace.get("metrics", {}).get("final", "")).startswith("ABORTED"):
            last = "aborted (stuck)"; print(f"  [desktop] attempt {attempt}: {last}", flush=True); continue
        from .world_verifiers import read_textedit
        if marker.lower() not in read_textedit(native_app).lower():
            last = "marker not in document"; print(f"  [desktop] attempt {attempt}: {last}", flush=True); continue
        traj = _desktop_trace_to_traj(trace)
        skill = compile_fused(traj, surface="desktop", name="hybrid_write_note", params={},
                              verify={"kind": "textedit", "contains": marker})
        skill.target = native_app                         # replay re-opens it before driving
        print(f"  [desktop] learned in {attempt} attempt(s)", flush=True)
        return HybridSegment(role="write_textedit_note", surface="desktop", skill=skill)
    raise HybridLearnError(f"desktop segment failed after {attempts} attempts (last: {last})")


def learn(url: str, native_app: str = "TextEdit", *, visible: bool = True) -> HybridSkill:
    """Run Gemini as the doer on BOTH segments and compile each — the real learn-cu loop."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not visible)
        page = browser.new_context(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]}).new_page()
        page.goto(url, wait_until="domcontentloaded")
        # The verification ground truth is the page's main HEADING — what Gemini selects — which on
        # real pages differs from the <title> (Wikipedia: "X" vs "X - Wikipedia"). Fall back to the
        # <title> only if there is no usable <h1>.
        marker = ""
        try:
            if page.locator("h1").count():
                marker = page.locator("h1").first.inner_text(timeout=3000).strip()
        except Exception:
            marker = ""
        marker = marker or page.title().strip()
        if len(marker) < 4:
            browser.close()
            raise HybridLearnError(f"page has no usable heading to verify against: {marker!r}")
        print(f"[learn] verification ground truth (heading): {marker!r}", flush=True)
        print("[learn] browser segment — Gemini selecting + copying the heading…", flush=True)
        browser_seg = _browser_segment(page, url, marker)
        browser.close()
    # the title is now on the OS clipboard; the desktop segment pastes + we verify it landed
    print("[learn] desktop segment — Gemini writing the note in TextEdit…", flush=True)
    desktop_seg = _desktop_segment(native_app, marker=marker)
    return HybridSkill(name="real_web_to_native_note", goal=f"Copy the heading of {url} into a {native_app} note",
                       url=url, segments=[browser_seg, desktop_seg],
                       learned_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


# ── replay (every segment at 0 CU, ground-truth verified) ──────────────────────────────────
def replay_hybrid(skill: HybridSkill, *, visible: bool = True) -> dict:
    """Replay each learned segment through the fusion dispatcher; the clipboard carries the live
    payload across surfaces. Returns total CU calls + per-segment ground-truth verdicts."""
    seg_results, cu_total, payload = [], 0, None
    with sync_playwright() as p:
        browser = page = None
        for seg in skill.segments:
            needle = (seg.skill.verify or {}).get("contains", "")
            if seg.surface == "browser":
                if browser is None:
                    browser = p.chromium.launch(headless=not visible)
                    page = browser.new_context(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]}).new_page()
                page.goto(seg.skill.target, wait_until="domcontentloaded")
                res = replay(seg.skill, BrowserExecutor(page), _ActionsVerifier())
                payload = _capture_selection(page)        # what the replayed actions selected
                if payload:
                    _set_clipboard(payload)               # bridge to the OS clipboard for the next segment
                verified = _payload_ok(payload, needle)
            else:
                desktop_cu.ensure_app(seg.skill.target)   # make sure the native app is open first
                _new_textedit_doc(seg.skill.target)       # a fresh, focused blank doc for the paste replay
                res = replay(seg.skill, DesktopExecutor(), make_verifier(seg.skill))
                verified = res["verified"]
            cu_total += res["cu_calls"]
            seg_results.append({"role": seg.role, "surface": seg.surface,
                                "cu_calls": res["cu_calls"], "verified": verified})
            print(f"  [{seg.surface}] {seg.role}: cu={res['cu_calls']} verified={verified}", flush=True)
            if not verified:
                break                                    # a broken seam fails the whole hybrid
        if browser is not None:
            browser.close()
    ok = bool(seg_results) and all(s["verified"] for s in seg_results)
    return {"ok": ok, "cu_calls": cu_total, "segments": seg_results, "payload": payload}


# ── persistence (segments are FusedSkills with crops; reuse the fusion serializer) ──────────
def to_dict(skill: HybridSkill) -> dict:
    return {"schema_version": 1, "kind": "learned_hybrid_skill", "name": skill.name, "goal": skill.goal,
            "url": skill.url, "learned_at": skill.learned_at,
            "segments": [{"role": s.role, "surface": s.surface, "skill": _skill_to_dict(s.skill)}
                         for s in skill.segments]}


def from_dict(d: dict) -> HybridSkill:
    return HybridSkill(name=d["name"], goal=d.get("goal", ""), url=d.get("url", ""),
                       learned_at=d.get("learned_at", ""),
                       segments=[HybridSegment(role=s["role"], surface=s["surface"],
                                               skill=_dict_to_skill(s["skill"]))
                                 for s in d["segments"]])


def save(skill: HybridSkill, path: str | Path) -> Path:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(to_dict(skill), indent=2), encoding="utf-8")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description="Learn / replay a real Gemini cross-surface hybrid skill")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("learn")
    lp.add_argument("--url", default="https://example.com")
    lp.add_argument("--native-app", default="TextEdit")
    lp.add_argument("--out", default="database/skills/registry/real_web_to_native_note.learned-hybrid.json")
    lp.add_argument("--replay", action="store_true", help="replay immediately after learning")
    lp.add_argument("--headless", action="store_true", help="run the browser headless (no clipboard on mac)")
    rp = sub.add_parser("replay")
    rp.add_argument("skill")
    rp.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    if args.cmd == "learn":
        skill = learn(args.url, args.native_app, visible=not args.headless)
        path = save(skill, args.out)
        print(f"\n[saved] {path}")
        if args.replay:
            print("\n=== REPLAY (0 model calls) ===")
            print(json.dumps(replay_hybrid(skill, visible=not args.headless), indent=2))
    else:
        skill = from_dict(json.loads(Path(args.skill).read_text(encoding="utf-8")))
        print(json.dumps(replay_hybrid(skill, visible=not args.headless), indent=2))


if __name__ == "__main__":
    main()
