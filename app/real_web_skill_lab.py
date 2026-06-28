"""Learn and replay safe real-web + native-Mac hybrid skills."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from .browser_backend import PlaywrightBrowserBackend
from .local_skill_registry import DEFAULT_SKILLS, LocalSkillRegistry
from .macro_skill import validate_macro
from .verification import check_final
from .verified_replay import MacOSDesktopBackend, replay_verified


DEFAULT_REAL_URL = "https://www.iana.org/domains/reserved"
DEFAULT_SKILL_PATH = DEFAULT_SKILLS / "real_web_textedit_note.hybrid.json"


@dataclass(frozen=True)
class PageSnapshot:
    url: str
    title: str
    heading: str
    marker: str


def _ascii(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii")


def _compact(value: str, *, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", _ascii(value)).strip()
    return text[:limit].rstrip()


def _marker(url: str, title: str) -> str:
    digest = hashlib.sha1(f"{url}\n{title}".encode("utf-8")).hexdigest()[:10]
    return f"rote-real-web-{digest}"


def capture_page(url: str, *, headless: bool = True) -> PageSnapshot:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_context(viewport={"width": 1280, "height": 720}).new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        title = _compact(page.title(), limit=120)
        heading = ""
        for selector in ("h1", "h2", "main p", "body"):
            try:
                heading = _compact(page.locator(selector).first.inner_text(timeout=3000), limit=180)
            except Exception:
                heading = ""
            if heading:
                break
        final_url = page.url
        browser.close()
    return PageSnapshot(
        url=final_url,
        title=title,
        heading=heading,
        marker=_marker(final_url, title),
    )


def note_text(snapshot: PageSnapshot) -> str:
    lines = [
        "Rote real web skill",
        f"Source: {snapshot.url}",
        f"Title: {snapshot.title}",
        f"Heading: {snapshot.heading}",
        f"Marker: {snapshot.marker}",
    ]
    return "\n".join(line for line in lines if line and not line.endswith(": "))


def build_browser_skill(snapshot: PageSnapshot) -> dict:
    condition = {"title_contains": snapshot.title or ""}
    if snapshot.heading:
        condition = {"all": [condition, {"text_contains": snapshot.heading[:80]}]}
    skill = {
        "schema_version": 2,
        "surface": "browser",
        "name": "real_web_read_page",
        "app": snapshot.url,
        "os": "any",
        "version": 1,
        "parent_version": None,
        "status": "active",
        "note": "Open and verify a real public web page without mutating it.",
        "params": {"url": snapshot.url, "title": snapshot.title, "heading": snapshot.heading},
        "checker": {"type": "condition", "condition": condition},
        "stats": {},
        "steps": [
            {"id": "open_real_page", "op": "navigate", "url": "{{url}}",
             "precondition": {}, "postcondition": condition,
             "timeout": 15, "retry_limit": 1, "fallback": [], "why": "Open the learned real web page"},
        ],
    }
    validate_macro(skill)
    return skill


def build_textedit_skill(snapshot: PageSnapshot) -> dict:
    text = note_text(snapshot)
    skill = {
        "schema_version": 2,
        "surface": "desktop",
        "name": "textedit_real_web_note",
        "app": "TextEdit",
        "os": "macos",
        "version": 1,
        "parent_version": None,
        "status": "active",
        "note": "Write a learned real-web research note into TextEdit.",
        "params": {"note_text": text, "marker": snapshot.marker},
        "checker": {
            "type": "condition",
            "condition": {"all": [
                {"foreground_app": "TextEdit"},
                {"textedit_document_contains": "{{marker}}"},
            ]},
        },
        "stats": {},
        "steps": [
            {"id": "open_textedit", "op": "open_app", "app": "TextEdit", "launch_wait": 6,
             "precondition": {}, "postcondition": {"foreground_app": "TextEdit"},
             "timeout": 6, "retry_limit": 1, "fallback": [], "why": "Open TextEdit"},
            {"id": "new_textedit_document", "op": "hotkey", "keys": ["command", "n"],
             "precondition": {"foreground_app": "TextEdit"},
             "postcondition": {"foreground_app": "TextEdit"},
             "timeout": 3, "retry_limit": 0, "fallback": [], "why": "Create a blank local note"},
            {"id": "write_real_web_note", "op": "type", "text": "{{note_text}}",
             "precondition": {"foreground_app": "TextEdit"},
             "postcondition": {"textedit_document_contains": "{{marker}}"},
             "timeout": 8, "retry_limit": 0, "fallback": [], "why": "Write the learned web summary"},
        ],
    }
    validate_macro(skill)
    return skill


def build_hybrid_skill(snapshot: PageSnapshot) -> dict:
    browser_skill = build_browser_skill(snapshot)
    textedit_skill = build_textedit_skill(snapshot)
    return {
        "schema_version": 1,
        "kind": "hybrid_skill",
        "name": "real_web_textedit_note",
        "status": "active",
        "note": "Read a real public website, then create a local TextEdit note from it.",
        "params": asdict(snapshot) | {"note_text": note_text(snapshot)},
        "segments": [
            {"id": "read_real_web_page", "surface": "browser", "skill": browser_skill},
            {"id": "write_textedit_note", "surface": "desktop", "skill": textedit_skill},
        ],
        "checker": {
            "type": "condition",
            "condition": {"textedit_document_contains": "{{marker}}"},
        },
        "learned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def validate_hybrid_skill(skill: dict) -> None:
    if skill.get("schema_version") != 1 or skill.get("kind") != "hybrid_skill":
        raise ValueError("unsupported hybrid skill schema")
    if not skill.get("name") or not isinstance(skill.get("segments"), list):
        raise ValueError("hybrid skill requires name and segments")
    seen = set()
    for segment in skill["segments"]:
        segment_id = segment.get("id")
        if not segment_id or segment_id in seen:
            raise ValueError(f"missing or duplicate segment id: {segment_id}")
        seen.add(segment_id)
        if segment.get("surface") not in {"browser", "desktop"}:
            raise ValueError(f"unsupported segment surface: {segment.get('surface')}")
        validate_macro(segment.get("skill", {}))


def save_hybrid_skill(skill: dict, path: str | Path = DEFAULT_SKILL_PATH) -> Path:
    validate_hybrid_skill(skill)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(skill, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return destination


def _summary(result: dict) -> dict:
    keys = (
        "success", "checker_passed", "checker_failures", "failed_step_id",
        "steps", "elapsed_s", "model_calls", "repair_calls", "mode",
        "skill_name", "skill_version",
    )
    return {key: result.get(key) for key in keys if key in result}


def replay_hybrid_skill(skill: dict, *, headless: bool = True, keep_open: float = 0.0) -> dict:
    validate_hybrid_skill(skill)
    started = time.time()
    segment_results = []
    with sync_playwright() as playwright:
        browser = None
        page = None
        for segment in skill["segments"]:
            segment_skill = segment["skill"]
            if segment["surface"] == "browser":
                if browser is None:
                    browser = playwright.chromium.launch(headless=headless)
                    page = browser.new_context(viewport={"width": 1280, "height": 720}).new_page()
                result = replay_verified(
                    segment_skill,
                    backend=PlaywrightBrowserBackend(page),
                    registry=LocalSkillRegistry(),
                    allow_repair=False,
                )
            else:
                result = replay_verified(
                    segment_skill,
                    backend=MacOSDesktopBackend(),
                    registry=LocalSkillRegistry(),
                    allow_repair=False,
                )
            segment_results.append({"id": segment["id"], "surface": segment["surface"], **_summary(result)})
            if not result.get("success"):
                break
        if page is not None and keep_open > 0:
            page.wait_for_timeout(keep_open * 1000)
        if browser is not None:
            browser.close()

    desktop_state = MacOSDesktopBackend().inspect()
    checker_passed, checker_failures = check_final(skill.get("checker"), skill.get("params", {}), desktop_state)
    ok = bool(segment_results and all(item.get("success") for item in segment_results) and checker_passed)
    return {
        "ok": ok,
        "skill": skill["name"],
        "segments": segment_results,
        "checker_passed": checker_passed,
        "checker_failures": checker_failures,
        "elapsed_s": round(time.time() - started, 2),
        "marker": skill.get("params", {}).get("marker"),
    }


def learn(url: str, *, out: str | Path = DEFAULT_SKILL_PATH, headless: bool = True) -> tuple[dict, Path]:
    snapshot = capture_page(url, headless=headless)
    skill = build_hybrid_skill(snapshot)
    return skill, save_hybrid_skill(skill, out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Learn or replay real-web + Mac app skills")
    sub = parser.add_subparsers(dest="command", required=True)
    learn_parser = sub.add_parser("learn")
    learn_parser.add_argument("--url", default=DEFAULT_REAL_URL)
    learn_parser.add_argument("--out", default=str(DEFAULT_SKILL_PATH))
    learn_parser.add_argument("--visible", action="store_true", help="show the browser while learning")
    learn_parser.add_argument("--replay", action="store_true", help="replay immediately after learning")
    learn_parser.add_argument("--keep-open", type=float, default=0.0)

    replay_parser = sub.add_parser("replay")
    replay_parser.add_argument("skill", nargs="?", default=str(DEFAULT_SKILL_PATH))
    replay_parser.add_argument("--visible", action="store_true", help="show the browser while replaying")
    replay_parser.add_argument("--keep-open", type=float, default=0.0)

    args = parser.parse_args()
    if args.command == "learn":
        skill, path = learn(args.url, out=args.out, headless=not args.visible)
        result: dict[str, Any] = {
            "ok": True,
            "learned": skill["name"],
            "path": str(path),
            "url": skill["params"]["url"],
            "title": skill["params"]["title"],
            "marker": skill["params"]["marker"],
        }
        if args.replay:
            result["replay"] = replay_hybrid_skill(
                skill, headless=not args.visible, keep_open=args.keep_open,
            )
        print(json.dumps(result, indent=2, default=str))
        raise SystemExit(0 if result.get("ok") and result.get("replay", {"ok": True}).get("ok") else 1)

    with open(args.skill, encoding="utf-8") as source:
        skill = json.load(source)
    result = replay_hybrid_skill(skill, headless=not args.visible, keep_open=args.keep_open)
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
