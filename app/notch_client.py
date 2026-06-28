"""Tiny, dependency-free client for the persistent notch companion.

The voice agent (asyncio) and the replay path send state updates to the notch *daemon* over a Unix
domain socket. This client is deliberately fire-and-forget: every call is best-effort and **silently
no-ops if no notch is listening**, so voice keeps working with or without the HUD.

Wire format: one JSON object per line (newline-delimited). Keys: mode + optional title/subtitle/
i/total/level. See app/notch_daemon.py for the renderer.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

# Shared by client and daemon. Kept under the system temp dir so it works for any user.
SOCKET_PATH = os.environ.get("ROTE_NOTCH_SOCK", os.path.join(tempfile.gettempdir(), "rote-notch.sock"))

VALID_MODES = {"idle", "listening", "thinking", "speaking", "working", "done", "error"}


class NotchClient:
    """Best-effort sender. Reconnects on demand; never raises into the caller."""

    def __init__(self, path: str = SOCKET_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None

    def _connect(self) -> socket.socket | None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(0.4)
            s.connect(self._path)
            return s
        except OSError:
            s.close()                                # don't leak the fd when no notch is listening
            return None

    def send(self, mode: str | None = None, **fields) -> None:
        """Send a state update. Unknown/None fields are dropped; failures are swallowed."""
        if mode is not None:
            fields["mode"] = mode
        payload = {k: v for k, v in fields.items() if v is not None}
        if not payload:
            return
        line = (json.dumps(payload) + "\n").encode("utf-8")
        with self._lock:
            for _ in range(2):                       # one cheap reconnect on a dropped socket
                if self._sock is None:
                    self._sock = self._connect()
                if self._sock is None:
                    return                           # no notch listening -> no-op
                try:
                    self._sock.sendall(line)
                    return
                except OSError:
                    try:
                        self._sock.close()
                    finally:
                        self._sock = None

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                finally:
                    self._sock = None


def ensure_daemon(path: str = SOCKET_PATH, timeout: float = 4.0):
    """Spawn the notch daemon if nothing is listening yet, and wait until the socket is up.

    Returns the spawned subprocess.Popen (so the caller can terminate it on exit), or None if a
    daemon was already running or AppKit isn't available."""
    probe = NotchClient(path)
    s = probe._connect()
    if s is not None:                                # already running
        s.close()
        return None
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "app.notch_daemon"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:                  # daemon died (e.g. no AppKit) -> give up
            return None
        s = probe._connect()
        if s is not None:
            s.close()
            return proc
        time.sleep(0.1)
    return proc
