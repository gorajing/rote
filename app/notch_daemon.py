"""Persistent notch companion daemon.

Owns the AppKit main thread (via NotchIsland) and listens on a Unix domain socket for newline-JSON
state updates from app/notch_client.py. One small process so the voice agent's asyncio loop and the
AppKit run loop never share a thread.

Run standalone:  python3 -m app.notch_daemon
The voice agent spawns this automatically (see app/notch_client.ensure_daemon).
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time

from .notch import NotchIsland, STATE
from .notch_client import SOCKET_PATH, VALID_MODES

IDLE_EXIT_AFTER = 30.0          # seconds with no client connected -> assume the agent is gone

_lock = threading.Lock()
_conns = 0
_last_activity = time.time()


def _sanitize(msg: dict) -> dict:
    """Whitelist + coerce incoming fields so a bad message can never crash the renderer."""
    out: dict = {}
    mode = msg.get("mode")
    if isinstance(mode, str) and mode in VALID_MODES:
        out["mode"] = mode
    for key in ("title", "subtitle"):
        if isinstance(msg.get(key), str):
            out[key] = msg[key]
    for key in ("i", "total"):
        try:
            if key in msg:
                out[key] = int(msg[key])
        except (TypeError, ValueError):
            pass
    if "level" in msg:
        try:
            out["level"] = max(0.0, min(1.0, float(msg["level"])))
        except (TypeError, ValueError):
            pass
    return out


def _handle(conn: socket.socket, island: NotchIsland) -> None:
    global _conns, _last_activity
    with _lock:
        _conns += 1
    buf = b""
    try:
        conn.settimeout(None)
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8", "ignore"))
                except Exception:
                    continue
                if isinstance(msg, dict):
                    fields = _sanitize(msg)
                    if fields:
                        island.update(**fields)
                        _last_activity = time.time()
    except OSError:
        pass
    finally:
        try:
            conn.close()
        finally:
            with _lock:
                _conns -= 1
            globals().__setitem__("_last_activity", time.time())


def _serve(island: NotchIsland) -> None:
    """Socket accept loop — runs on NotchIsland's worker thread."""
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCKET_PATH)
    srv.listen(8)
    srv.settimeout(1.0)
    threading.Thread(target=_watchdog, daemon=True).start()
    while True:
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        threading.Thread(target=_handle, args=(conn, island), daemon=True).start()


def _watchdog() -> None:
    """Exit cleanly if no client is connected for a while (the agent has gone)."""
    from AppKit import NSApplication
    while True:
        time.sleep(5.0)
        with _lock:
            idle = _conns == 0 and (time.time() - _last_activity) > IDLE_EXIT_AFTER
        if idle:
            try:
                os.unlink(SOCKET_PATH)
            except OSError:
                pass
            NSApplication.sharedApplication().performSelectorOnMainThread_withObject_waitUntilDone_(
                "terminate:", None, False)
            return


def main() -> None:
    island = NotchIsland()
    STATE.update(mode="idle", title="Rote", subtitle="")
    island.serve(lambda: _serve(island))      # blocks in AppKit's run loop


if __name__ == "__main__":
    main()
