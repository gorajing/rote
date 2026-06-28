"""Headless tests for the notch voice companion's protocol path.

No AppKit window and no microphone: we exercise the daemon's message sanitizer and the
client -> socket -> handler -> island update flow against a fake island. This is the coverage the
original one-shot notch never had.
"""
import os
import socket
import tempfile
import threading
import time
import unittest

from app import notch_daemon
from app.notch_client import NotchClient


class FakeIsland:
    """Stands in for NotchIsland.update so we can assert what the daemon would render."""
    def __init__(self):
        self.updates = []

    def update(self, **kw):
        self.updates.append(kw)


class SanitizeTests(unittest.TestCase):
    def test_valid_mode_and_fields_pass_through(self):
        out = notch_daemon._sanitize(
            {"mode": "working", "title": "Saving", "subtitle": "step 3 of 7", "i": 3, "total": 7})
        self.assertEqual(out, {"mode": "working", "title": "Saving",
                               "subtitle": "step 3 of 7", "i": 3, "total": 7})

    def test_unknown_mode_is_dropped_but_other_fields_kept(self):
        out = notch_daemon._sanitize({"mode": "explode", "title": "x"})
        self.assertNotIn("mode", out)
        self.assertEqual(out["title"], "x")

    def test_level_is_clamped_and_numbers_coerced(self):
        self.assertEqual(notch_daemon._sanitize({"level": 5.0})["level"], 1.0)
        self.assertEqual(notch_daemon._sanitize({"level": -2})["level"], 0.0)
        self.assertEqual(notch_daemon._sanitize({"i": "4"})["i"], 4)

    def test_garbage_types_are_ignored(self):
        out = notch_daemon._sanitize({"title": 123, "i": "not-a-number", "total": None})
        self.assertEqual(out, {})


class ClientTests(unittest.TestCase):
    def test_send_is_a_noop_without_a_server(self):
        path = os.path.join(tempfile.gettempdir(), "rote-notch-missing-%d.sock" % os.getpid())
        client = NotchClient(path)
        client.send("listening", title="Listening…")     # must not raise
        client.close()

    def test_client_messages_reach_the_island_sanitized(self):
        path = os.path.join(tempfile.mkdtemp(), "notch.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        island = FakeIsland()

        def accept_one():
            conn, _ = srv.accept()
            notch_daemon._handle(conn, island)

        worker = threading.Thread(target=accept_one, daemon=True)
        worker.start()

        client = NotchClient(path)
        client.send("listening", title="Listening…", subtitle="calculate 52 times 68")
        client.send("working", i=2, total=5, title="Saving to Desktop")
        client.send("nonsense_mode", title="kept")        # mode dropped, title kept
        client.close()                                    # EOF -> handler returns
        worker.join(timeout=2.0)
        srv.close()

        modes = [u.get("mode") for u in island.updates]
        self.assertIn("listening", modes)
        self.assertIn("working", modes)
        self.assertEqual(island.updates[0]["subtitle"], "calculate 52 times 68")
        working = next(u for u in island.updates if u.get("mode") == "working")
        self.assertEqual((working["i"], working["total"]), (2, 5))
        self.assertTrue(any("mode" not in u and u.get("title") == "kept" for u in island.updates))


if __name__ == "__main__":
    unittest.main()
