#!/usr/bin/env python3
"""Local Skill lookup and MongoDB Atlas vector search APIs.

The local ``query`` API remains available for deterministic, offline Skill
lookup. ``push`` and ``retrieve`` provide semantic storage and search using
Gemini embeddings stored in MongoDB Atlas.

Python examples::

    query(platform="web", address="amazon", load_skill=True)
    push({"title": "Amazon", "description": "Buy headphones"})
    retrieve("how to buy headphones", top_k=3)

CLI examples::

    python database/api.py local web --address amazon --skill
    python database/api.py push '{"description": "buy headphones"}'
    python database/api.py retrieve "how to buy headphones" --top-k 3
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from google import genai
from pymongo import MongoClient
from pymongo.collection import Collection

# Allow running as ``python database/api.py`` from the repository root.
sys.path.insert(0, str(Path(__file__).parent.parent))
from app import config  # noqa: E402
from app.schemas import Skill  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_DB_DIR = Path(__file__).parent
_INDEX_PATH = _DB_DIR / "index.json"
_DATA_DIR = _DB_DIR / "data"
_VECTOR_INDEX = "description"
_VECTOR_PATH = "embedding"
_EMBED_MODEL = "gemini-embedding-2"

VALID_PLATFORMS = {"web", "excel", "adobe", "apple_email", "whatsapp"}
VALID_PURPOSES = {"buy", "generate", "communicate"}

_mongo_client: Optional[MongoClient] = None
_genai_client: Optional[genai.Client] = None


def _load_index() -> list[dict]:
    with open(_INDEX_PATH, encoding="utf-8") as f:
        return json.load(f)["entries"]


def _entry_to_skill(entry: dict) -> Skill:
    data = json.loads((_DATA_DIR / entry["filename"]).read_text(encoding="utf-8"))
    return Skill(**data)


def query(
    platform: str,
    address: Optional[str] = None,
    purpose: Optional[str] = None,
    load_skill: bool = False,
) -> list[dict | Skill]:
    """Query the repository's local Skill index by exact metadata."""
    if platform not in VALID_PLATFORMS:
        raise ValueError(
            f"Invalid platform '{platform}'. Choose from: {', '.join(sorted(VALID_PLATFORMS))}"
        )
    if purpose is not None and purpose not in VALID_PURPOSES:
        raise ValueError(
            f"Invalid purpose '{purpose}'. Choose from: {', '.join(sorted(VALID_PURPOSES))}"
        )

    results = []
    for entry in _load_index():
        if entry["platform"] != platform:
            continue
        if address is not None and entry.get("address") != address:
            continue
        if purpose is not None and entry["purpose"] != purpose:
            continue
        results.append(_entry_to_skill(entry) if load_skill else dict(entry))
    return results


def _genai() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client()
    return _genai_client


def _embed(text: str) -> list[float]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Text to embed must be a non-empty string")
    result = _genai().models.embed_content(model=_EMBED_MODEL, contents=text)
    return result.embeddings[0].values


def _collection() -> Collection:
    global _mongo_client
    if _mongo_client is None:
        if not config.MONGO_URI:
            raise RuntimeError("ROTE_MONGO_URI is not set in environment")
        _mongo_client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000,
        )
    return _mongo_client[config.DB_NAME][config.INSTRUCTIONS_COLLECTION]


def push(doc: dict) -> str:
    """Insert or upsert a document and its description embedding."""
    if not isinstance(doc, dict):
        raise TypeError("Document must be a dictionary")
    description = doc.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("Document must include a non-empty 'description' field")

    document = {**doc, _VECTOR_PATH: _embed(description)}
    collection = _collection()
    if "_id" in document:
        collection.replace_one({"_id": document["_id"]}, document, upsert=True)
        return str(document["_id"])
    return str(collection.insert_one(document).inserted_id)


def push_many(docs: list[dict]) -> list[str]:
    """Push documents in order and return their IDs."""
    return [push(doc) for doc in docs]


def retrieve(search_text: str, top_k: int = 5, filters: Optional[dict] = None) -> list[dict]:
    """Return documents ordered by Atlas vector-search similarity."""
    if not isinstance(search_text, str) or not search_text.strip():
        raise ValueError("search_text must be a non-empty string")
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    if top_k > 50:
        raise ValueError("top_k must not exceed 50")
    vector_search = {
        "index": _VECTOR_INDEX,
        "path": _VECTOR_PATH,
        "queryVector": _embed(search_text),
        "numCandidates": max(100, top_k * 10),
        "limit": top_k,
    }
    if filters:
        clauses = [{key: value} for key, value in filters.items() if value is not None]
        if clauses:
            vector_search["filter"] = clauses[0] if len(clauses) == 1 else {"$and": clauses}
    pipeline = [
        {"$vectorSearch": vector_search},
        {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
        {"$project": {_VECTOR_PATH: 0}},
    ]
    return list(_collection().aggregate(pipeline))


def _print_local_results(results: list[dict | Skill]) -> None:
    if not results:
        print("No matching entries found.")
        return
    for item in results:
        if isinstance(item, Skill):
            print(f"{item.name}: {item.goal_template} ({item.status}, v{item.version})")
        else:
            print(
                f"{item['id']}: {item['skill_name']} "
                f"({item['platform']}, {item['status']}, v{item['version']})"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Rote local and vector database CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    local = sub.add_parser("local", help="Query the repository's local Skill index")
    local.add_argument("platform", choices=sorted(VALID_PLATFORMS))
    local.add_argument("--address", metavar="SITE")
    local.add_argument("--purpose", choices=sorted(VALID_PURPOSES))
    local.add_argument("--skill", action="store_true", help="Load full Skill objects")

    push_parser = sub.add_parser("push", help="Push a JSON document to Atlas")
    push_parser.add_argument("doc", help='JSON object with a "description" field')

    retrieve_parser = sub.add_parser("retrieve", help="Search Atlas semantically")
    retrieve_parser.add_argument("search_text", help="Natural-language search text")
    retrieve_parser.add_argument("--top-k", type=int, default=5, metavar="N")
    argv = sys.argv[1:]
    # Preserve the original CLI form: ``database/api.py web --address amazon``.
    if argv and argv[0] in VALID_PLATFORMS:
        argv.insert(0, "local")
    args = parser.parse_args(argv)

    if args.command == "local":
        _print_local_results(query(args.platform, args.address, args.purpose, args.skill))
    elif args.command == "push":
        print(f"Pushed: {push(json.loads(args.doc))}")
    else:
        for result in retrieve(args.search_text, args.top_k):
            print(json.dumps(result, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
