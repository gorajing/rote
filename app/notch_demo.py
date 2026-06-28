"""Visual smoke test for the notch companion — drives it through every state, no mic / no LiveKit.

  python3 -m app.notch_demo

It auto-starts the notch daemon, then walks: listening (with a transcript) → thinking → speaking →
the working ring filling step by step → the green-check 'done' → back to the idle pill. Watch the
MacBook notch. Quit the daemon afterward with: pkill -f app.notch_daemon
"""
import time

from .notch_client import NotchClient, ensure_daemon


def main() -> None:
    if ensure_daemon() is None:
        # already running, or PyObjC missing — either way just try to drive whatever is listening
        pass
    notch = NotchClient()
    time.sleep(0.6)

    notch.send("listening", title="Listening…", subtitle="calculate 52 times 68")
    time.sleep(2.0)
    notch.send("thinking", title="Thinking…")
    time.sleep(1.2)
    notch.send("speaking", title="Speaking")
    time.sleep(1.4)

    total = 7
    for i in range(1, total + 1):
        title = "Opening Calculator" if i == 1 else "Saving to Desktop" if i >= total - 1 else "Working…"
        notch.send("working", i=i, total=total, title=title)
        time.sleep(0.5)

    notch.send("done", title="Done")
    time.sleep(2.2)                          # daemon collapses the check back to the pill on its own
    notch.send("idle", title="Rote")
    time.sleep(0.5)
    notch.close()
    print("Demo done. The pill stays until the daemon idles out (~30s) or: pkill -f app.notch_daemon")


if __name__ == "__main__":
    main()
