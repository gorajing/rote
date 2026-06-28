"""Semantic Playwright backend for the shared verified replay and repair engine."""
from __future__ import annotations

import time


class PlaywrightBrowserBackend:
    def __init__(self, page):
        self.page = page

    def _locator(self, target: dict):
        if "role" in target:
            locator = self.page.get_by_role(target["role"], name=target.get("name"))
        elif "label" in target:
            locator = self.page.get_by_label(target["label"])
        elif "text" in target:
            locator = self.page.get_by_text(target["text"], exact=target.get("exact", True))
        elif "testid" in target:
            locator = self.page.get_by_test_id(target["testid"])
        else:
            locator = self.page.locator(target["css"])
        return locator.first if target.get("first") else locator

    def execute(self, step: dict) -> dict:
        op = step["op"]
        timeout = int(float(step.get("timeout", 5)) * 1000)
        if op == "navigate":
            self.page.goto(step["url"], wait_until="domcontentloaded", timeout=timeout)
            # Modern SPAs often paint meaningful content after DOMContentLoaded. Give the page a
            # short bounded hydration window before the replay engine inspects postconditions.
            self.page.wait_for_timeout(min(2000, timeout))
        elif op == "wait":
            self.page.wait_for_timeout(float(step.get("seconds", 1)) * 1000)
        elif op == "press":
            self.page.keyboard.press(step["key"])
        elif op == "scroll":
            self.page.mouse.wheel(int(step.get("dx", 0)), int(step.get("dy", 500)))
        else:
            locator = self._locator(step["target"])
            if op == "click":
                locator.click(timeout=timeout)
            elif op == "fill":
                locator.fill(step["text"], timeout=timeout)
            elif op == "select":
                locator.select_option(step["value"], timeout=timeout)
            elif op == "check":
                locator.check(timeout=timeout)
            elif op == "uncheck":
                locator.uncheck(timeout=timeout)
            else:
                raise ValueError(f"unsupported browser operation: {op}")
        return {}

    def inspect(self) -> dict:
        try:
            visible_text = self.page.locator("body").inner_text(timeout=3000)
        except Exception:
            visible_text = ""
        return {
            "url": self.page.url,
            "title": self.page.title(),
            "visible_text": visible_text,
        }

    def screenshot_png(self) -> bytes:
        return self.page.screenshot(type="png")
