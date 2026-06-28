"""Ground-truth verifiers for REAL-WORLD (non-arena) hybrid skills.

The fusion Verifier protocol is `check(skill) -> bool`, reading GROUND TRUTH and never the
model's self-report. The arena has `/state`; the real world does not, so we read stable OS
facts instead:

    ClipboardVerifier : the OS clipboard holds the expected text — proves a browser "copy"
                        segment actually captured the live value (the cross-surface payload).
    TextEditVerifier  : the front TextEdit document contains the expected text — proves a
                        desktop "note" segment actually wrote it.

Both fail CLOSED (any error -> False) and make ZERO model calls, exactly like the arena
verifiers in verifier.py. The expected text rides in `skill.verify["contains"]`.
"""
from __future__ import annotations

import subprocess

from .contract import FusedSkill


def read_clipboard() -> str:
    """The macOS pasteboard as text (the channel a browser Cmd+C writes and a desktop Cmd+V
    reads). Empty string on any failure."""
    try:
        return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""


def _contains(haystack: str, needle: str) -> bool:
    needle = (needle or "").strip()
    if len(needle) < 4:                  # too short to certify a payload (avoids 'Home'-type false hits)
        return False
    return needle.lower() in str(haystack).strip().lower()


class ClipboardVerifier:
    """Browser "copy" segment: the OS clipboard contains the expected text."""

    def check(self, skill: FusedSkill) -> bool:
        try:
            return _contains(read_clipboard(), (skill.verify or {}).get("contains", ""))
        except Exception:
            return False


def read_textedit(app: str = "TextEdit") -> str:
    """Front-document text of a native text app via a SINGLE osascript — robust (own timeout, never
    raises). Deliberately NOT MacOSDesktopBackend.inspect(), which queries many apps (incl. a Word
    AppleScript that can hang for 5s and crash the whole check)."""
    try:
        r = subprocess.run(
            ["osascript", "-e",
             f'if application "{app}" is running then tell application "{app}" '
             f'to if (count of documents) > 0 then return text of front document'],
            capture_output=True, text=True, timeout=8)
        return r.stdout or ""
    except Exception:
        return ""


class TextEditVerifier:
    """Desktop "note" segment: the front TextEdit document contains the expected text. Pure ground
    truth, independent of HOW the note was typed."""

    def check(self, skill: FusedSkill) -> bool:
        try:
            return _contains(read_textedit(), (skill.verify or {}).get("contains", ""))
        except Exception:
            return False
