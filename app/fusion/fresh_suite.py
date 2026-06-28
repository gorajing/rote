"""A curated suite of BRAND-NEW real-world hybrid skills — fresh public websites paired with a Mac
app, none from the AcmeBilling arena or the desktop-Word pipeline. Each runs the genuinely-learned
hybrid loop (Gemini does it once on each surface → compile → 0-CU verified replay) and stores the
result, growing the recall DB. This is "hybrid tool use" tested on fresh real-world tasks.

    python -m app.fusion.fresh_suite                 # run the whole suite (live; real Gemini CU on learn)
    python -m app.fusion.fresh_suite --only python-org
    python -m app.fusion.fresh_suite --headless      # browser headless (desktop still drives the real Mac)

The browser segment (read a real page, copy its heading) is robust; the desktop segment (Gemini
driving a Mac app to paste) is the bottleneck — desktop CU is fumbly and can hit a model safety
block, so it is RETRIED, and only a run that actually lands the heading in the document is compiled.
"""
from __future__ import annotations

import argparse
import json

from .hybrid import HybridLearnError, learn, replay_hybrid, save

# Fresh public sites × a Mac app. All read-only on the web; nothing logs in or mutates a site.
SUITE = [
    {"id": "computer-mouse", "url": "https://en.wikipedia.org/wiki/Computer_mouse", "app": "TextEdit",
     "desc": "Save a Wikipedia article's heading into a TextEdit note"},
    {"id": "python-org", "url": "https://www.python.org", "app": "TextEdit",
     "desc": "Save python.org's headline into a TextEdit note"},
    {"id": "hacker-news", "url": "https://news.ycombinator.com", "app": "TextEdit",
     "desc": "Save the Hacker News page heading into a TextEdit note"},
    {"id": "iana-example", "url": "https://www.iana.org/help/example-domains", "app": "TextEdit",
     "desc": "Save IANA's example-domains heading into a TextEdit note"},
]


def run_one(spec: dict, *, visible: bool = True) -> dict:
    """Learn (real Gemini, both surfaces) → store → replay at 0 CU, verified. Returns a result row."""
    try:
        skill = learn(spec["url"], spec["app"], visible=visible)
        save(skill, f"database/skills/registry/fresh_{spec['id']}.learned-hybrid.json")
        res = replay_hybrid(skill, visible=visible)
        return {"id": spec["id"], "ok": res["ok"], "cu_calls": res["cu_calls"], "payload": res.get("payload")}
    except HybridLearnError as exc:
        return {"id": spec["id"], "ok": False, "error": str(exc)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the fresh real-world hybrid skill suite (live)")
    ap.add_argument("--only", help="run a single skill by id")
    ap.add_argument("--headless", action="store_true", help="browser headless (desktop still uses the real Mac)")
    args = ap.parse_args()

    suite = [s for s in SUITE if not args.only or s["id"] == args.only]
    rows = []
    for spec in suite:
        print(f"\n=== {spec['id']}: {spec['desc']} ===", flush=True)
        rows.append(run_one(spec, visible=not args.headless))
        print(json.dumps(rows[-1]), flush=True)

    ok = sum(1 for r in rows if r.get("ok"))
    print(f"\n=== FRESH SUITE: {ok}/{len(rows)} learned + replayed at 0 CU, verified ===")
    for r in rows:
        tail = f"cu={r.get('cu_calls')} payload={r.get('payload')!r}" if r.get("ok") else r.get("error", "")[:70]
        print(f"  {'OK ' if r.get('ok') else 'XX '} {r['id']:<16} {tail}")


if __name__ == "__main__":
    main()
