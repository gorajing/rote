"""Concrete Verifier implementations (contract.Verifier) — ground-truth success checks, never
the model's self-report. Pick one with make_verifier(skill); both fail CLOSED (any error -> False).

  CheckerVerifier  : browser — reconstruct a Task from skill.verify and call app.checker.check
                     (reads the arena /state).
  ArtifactVerifier : desktop — read a produced artifact (.docx zip/xml, or plain text) and
                     confirm it contains the expected text.
"""
from __future__ import annotations

import os
import zipfile

from .contract import FusedSkill


class CheckerVerifier:
    """Browser surface: verify against the arena /state via app.checker."""

    def check(self, skill: FusedSkill) -> bool:
        try:
            from .. import checker
            from ..schemas import Task
            v = skill.verify or {}
            task = Task(id="fusion-verify", site=v.get("site", "billing"), intent="",
                        params=dict(v.get("params", {})), checker=v["checker"],
                        family=v.get("family", ""))
            return bool(checker.check(task))
        except Exception:
            return False


class ArtifactVerifier:
    """Desktop surface: verify a produced file contains the expected text (.docx or plain)."""

    def check(self, skill: FusedSkill) -> bool:
        try:
            v = skill.verify or {}
            path = os.path.expanduser(v["path"])
            needle = v.get("contains", "")
            if not os.path.exists(path):
                return False
            if path.lower().endswith(".docx"):
                with zipfile.ZipFile(path) as z:
                    xml = z.read("word/document.xml").decode("utf-8", "ignore")
                return needle in xml
            with open(path, encoding="utf-8", errors="ignore") as f:
                return needle in f.read()
        except Exception:
            return False


def make_verifier(skill: FusedSkill):
    """Return the right Verifier for a skill's verify spec (defaults to the /state checker)."""
    kind = (skill.verify or {}).get("kind", "checker")
    if kind in ("docx", "file", "artifact"):
        return ArtifactVerifier()
    if kind == "clipboard":
        from .world_verifiers import ClipboardVerifier
        return ClipboardVerifier()
    if kind == "textedit":
        from .world_verifiers import TextEditVerifier
        return TextEditVerifier()
    return CheckerVerifier()
