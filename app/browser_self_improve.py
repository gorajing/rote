"""Run the shared verified replay/repair loop against Playwright browser skills."""
from __future__ import annotations

import argparse
import json
import urllib.request

from playwright.sync_api import sync_playwright

from .browser_backend import PlaywrightBrowserBackend
from .local_skill_registry import LocalSkillRegistry
from .macro_skill import resolve_params
from .skill_repair import RepairService
from .verified_replay import replay_verified


def _reset(skill: dict, params: dict, page) -> None:
    reset = resolve_params(skill.get("reset", {}), params)
    if reset.get("type") == "http":
        request = urllib.request.Request(reset["url"], method=reset.get("method", "POST"))
        urllib.request.urlopen(request, timeout=float(reset.get("timeout", 5))).close()
    start_url = reset.get("start_url") or skill.get("start_url")
    if start_url:
        page.goto(resolve_params(start_url, params), wait_until="domcontentloaded")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rote browser verified replay and repair")
    parser.add_argument("command", choices=("replay", "repair"))
    parser.add_argument("skill")
    parser.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--keep-open", type=float, default=0,
                        help="seconds to keep the visible browser open after execution")
    args = parser.parse_args()
    overrides = dict(item.split("=", 1) for item in args.param)
    registry = LocalSkillRegistry()
    skill = registry.load_skill(args.skill)
    if skill.get("surface") != "browser":
        raise SystemExit(f"{args.skill} is not a browser skill")
    params = {**skill.get("params", {}), **overrides}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=args.headless)
        page = browser.new_context(viewport={"width": 1280, "height": 720}).new_page()
        _reset(skill, params, page)
        backend = PlaywrightBrowserBackend(page)
        service = RepairService(
            registry=registry,
            reset=lambda values: _reset(skill, values, page),
        ) if args.command == "repair" else None
        result = replay_verified(
            skill, params, allow_repair=service is not None, backend=backend,
            registry=registry, repair_service=service,
        )
        registry.record_run(skill, result)
        if args.keep_open > 0:
            page.wait_for_timeout(args.keep_open * 1000)
        browser.close()
    print(json.dumps({k: v for k, v in result.items() if k != "failure"}, indent=2, default=str))
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
