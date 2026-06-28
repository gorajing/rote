"""Recall — a plain-language intent -> the verified skill learned for it -> replay at 0 CU.

This closes the loop the system was missing: the vector DB held only mock seed data and NOTHING
in app/ read it, so a learned skill could never be found by meaning. Now:

    intent  ->  recall(intent)  ->  FusionSkillStore.load_active  ->  fusion replay at 0 CU, verified.

LOCAL-FIRST by design: every promoted skill is embedded into a local index (recall_index.json
next to the fusion store) and matched by cosine similarity in-process. Recall touches the network
only to embed the *query* string — a cheap TEXT embedding, NOT a computer-use call, so replay stays
0 CU. If MongoDB Atlas is configured (database/api.py) a skill can also be pushed there for
cross-agent sharing, but recall never depends on it (no stage-wifi single point of failure).

Additive: this is one new module. Promotion callers opt in by calling `index_skill(name, intent)`
after `FusionSkillStore.save_promoted(...)`; the store itself stays pure. `backfill_from_tasks()`
seeds the index over the existing arena library in one shot.

    python -m app.fusion.recall --backfill
    python -m app.fusion.recall "refund the paid Globex invoice"
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .. import config as _config  # noqa: F401 — importing config loads .env (the embedding API key)
from .skill_store import _DEFAULT_ROOT, FusionSkillStore

_EMBED_MODEL = "gemini-embedding-2"          # the model database/api.py already standardized on
_INDEX_NAME = "recall_index.json"


_client = None


def _client_lazy():
    """One reused genai client (constructing one per call can hit a closed-client error)."""
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client()
    return _client


def _embed(text: str) -> list[float]:
    """Embed a string with Gemini's text-embedding model. A cheap text call — never computer-use."""
    result = _client_lazy().models.embed_content(model=_EMBED_MODEL, contents=text)
    return list(result.embeddings[0].values)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _index_path(root) -> Path:
    return Path(root) / _INDEX_NAME


def _load_index(root) -> list[dict]:
    p = _index_path(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _save_index(entries: list[dict], root) -> None:
    p = _index_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    tmp.replace(p)                                 # atomic: never leave a half-written index


def index_skill(name: str, intent: str, *, root=_DEFAULT_ROOT) -> None:
    """Embed a skill's intent and upsert it into the local recall index (idempotent by name)."""
    entries = [e for e in _load_index(root) if e.get("name") != name]
    entries.append({"name": name, "intent": intent, "embedding": _embed(intent)})
    _save_index(entries, root)


def recall(intent: str, *, top_k: int = 3, root=_DEFAULT_ROOT) -> list[dict]:
    """The top_k learned skills whose stored intent best matches `intent`, by cosine similarity."""
    q = _embed(intent)
    scored = [{"name": e["name"], "intent": e["intent"], "score": _cosine(q, e["embedding"])}
              for e in _load_index(root)]
    scored.sort(key=lambda e: e["score"], reverse=True)
    return scored[:top_k]


def recall_load(intent: str, *, store: FusionSkillStore | None = None, root=_DEFAULT_ROOT):
    """Recall the best match and load it from the fusion store. Returns (FusedSkill | None, match)."""
    matches = recall(intent, top_k=1, root=root)
    if not matches:
        return None, None
    store = store or FusionSkillStore(root)
    return store.load_active(matches[0]["name"]), matches[0]


def promote_and_index(store: FusionSkillStore, skill, intent: str, *, verified: bool,
                      cu_calls: int = 0, reason: str = "promote", root=_DEFAULT_ROOT) -> dict:
    """Promote a verified skill AND index its intent for recall — the single promotion entrypoint so
    every learned skill is immediately recallable (closes the 'recalled in plain language' gap)."""
    rec = store.save_promoted(skill, verified=verified, cu_calls=cu_calls, reason=reason)
    index_skill(skill.name, intent, root=root)
    return rec


def run_with_recall(intent: str, executor, verifier, *, store: FusionSkillStore | None = None,
                    root=_DEFAULT_ROOT, heal: bool = True) -> dict:
    """The CLOSED self-improving loop: recall a learned skill by intent -> replay at 0 CU, SELF-
    HEALING on drift -> PERSIST the heal as a new version iff it re-verified, so the NEXT run is
    back to 0 CU. On a recall miss returns needs_cold_learn=True (the caller cold-learns then
    promote_and_index). The recalled skill is already indexed, so a heal only bumps its version —
    no re-index needed."""
    from .dispatch import replay                       # lazy: keep cv2 off recall's index/query path
    store = store or FusionSkillStore(root)
    skill, match = recall_load(intent, store=store, root=root)
    if skill is None:
        return {"recalled": None, "needs_cold_learn": True, "verified": False, "cu_calls": 0, "healed": []}
    res = replay(skill, executor, verifier, heal=heal)
    if heal and res["verified"] and res["healed"]:
        store.save_promoted(skill, verified=True, cu_calls=res["cu_calls"],
                            reason=f"self-heal: re-grounded steps {res['healed']}")
    return {"recalled": match["name"], "score": match["score"], "needs_cold_learn": False, **res}


def backfill_from_tasks(*, root=_DEFAULT_ROOT) -> int:
    """One-shot seed: index every stored fusion skill using its arena task intent. Skills are named
    fused_<task_id>, so we recover the goal text from app.tasks without re-promoting anything."""
    from ..tasks import SPLITS
    by_id = {t.id: t for t in SPLITS["all"]}
    fusion_index = Path(root) / "fusion_index.json"
    names = json.loads(fusion_index.read_text(encoding="utf-8")).get("skills", {}) if fusion_index.exists() else {}
    n = 0
    for name in names:
        task = by_id.get(name.removeprefix("fused_"))
        if task is not None:
            index_skill(name, task.intent, root=root)
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="Recall a learned skill by plain-language intent")
    ap.add_argument("intent", nargs="?", help="the goal to recall a skill for")
    ap.add_argument("--backfill", action="store_true", help="seed the index from the stored arena skills")
    ap.add_argument("--top-k", type=int, default=3)
    args = ap.parse_args()
    if args.backfill:
        print(f"indexed {backfill_from_tasks()} skills")
    if args.intent:
        for m in recall(args.intent, top_k=args.top_k):
            print(f"  {m['score']:.3f}  {m['name']:<34} {m['intent'][:64]}")


if __name__ == "__main__":
    main()
