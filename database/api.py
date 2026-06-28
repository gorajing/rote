#!/usr/bin/env python3
"""
Rote Database API — MongoDB Atlas vector store with Voyage AI automated embeddings.

Atlas handles embedding generation automatically — no embedding library needed here.

Atlas setup (one-time):
    1. Cluster → Search → Embedding Providers → Add Voyage AI API key
    2. Create a Vector Search index on rote.instructions named "embedding_index":
       {
         "fields": [{
           "type": "autoEmbed",
           "modality": "text",
           "path": "description",
           "model": "voyage-4-lite"
         }]
       }

Usage (Python):
    from database.api import push, retrieve

    push({
        "title": "Buy Headphones on Amazon",
        "description": "Purchase headphones on Amazon using filters and checkout",
        "platform": "web",
        "content": "Step 1: ...",
    })

    results = retrieve("how to buy something on amazon", top_k=3)

Usage (CLI):
    python api.py push '{"title": "...", "description": "...", "content": "..."}'
    python api.py retrieve "how to buy headphones" --top-k 5
"""
import json
import os
import argparse
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from google import genai
from pymongo import MongoClient
from pymongo.collection import Collection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_MONGO_URI = os.getenv("ROTE_MONGO_URI", "")
_DB_NAME = "automated_tasks"
_COLLECTION = "tasks"
_VECTOR_INDEX = "description"
_EMBED_MODEL = "gemini-embedding-2"

_mongo_client: Optional[MongoClient] = None
_genai_client: Optional[genai.Client] = None


def _genai() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client()
    return _genai_client


def _embed(text: str) -> list[float]:
    result = _genai().models.embed_content(model=_EMBED_MODEL, contents=text)
    return result.embeddings[0].values


def _collection() -> Collection:
    global _mongo_client
    if _mongo_client is None:
        if not _MONGO_URI:
            raise RuntimeError("ROTE_MONGO_URI is not set in environment")
        _mongo_client = MongoClient(_MONGO_URI)
    return _mongo_client[_DB_NAME][_COLLECTION]


def push(doc: dict) -> str:
    """
    Insert or upsert a document. Atlas automatically generates the embedding
    from the 'description' field via Voyage AI.

    Args:
        doc: Any dict with at least a 'description' key. If '_id' is present,
             performs an upsert; otherwise inserts a new document.

    Returns:
        The document _id as a string.
    """
    if "description" not in doc:
        raise ValueError("Document must include a 'description' field")

    document = {**doc, "embedding": _embed(doc["description"])}
    col = _collection()

    if "_id" in document:
        col.replace_one({"_id": document["_id"]}, document, upsert=True)
        return str(document["_id"])

    result = col.insert_one(document)
    return str(result.inserted_id)


def push_many(docs: list[dict]) -> list[str]:
    """Batch push. Returns list of _id strings in insertion order."""
    return [push(doc) for doc in docs]


def retrieve(query: str, top_k: int = 5) -> list[dict]:
    """
    Semantic search. Atlas embeds the query text automatically — no API calls
    needed in the app.

    Args:
        query:  Natural-language query matched against stored descriptions.
        top_k:  Maximum number of results (default 5).

    Returns:
        List of matching documents ordered by similarity descending,
        each with a 'score' field.
    """
    pipeline = [
        {
            "$vectorSearch": {
                "index": _VECTOR_INDEX,
                "path": "embedding",
                "queryVector": _embed(query),
                "numCandidates": top_k * 10,
                "limit": top_k,
            }
        },
        {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
        {"$project": {"embedding": 0}},
    ]
    return list(_collection().aggregate(pipeline))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rote vector database CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python api.py push '{\"description\": \"buy headphones\", \"title\": \"Amazon\"}'\n"
            "  python api.py retrieve \"how to buy something on amazon\" --top-k 3\n"
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_push = sub.add_parser("push", help="Push a document (JSON string)")
    p_push.add_argument("doc", help='JSON object with at least a "description" field')

    p_ret = sub.add_parser("retrieve", help="Semantic similarity search")
    p_ret.add_argument("query", help="Natural-language query")
    p_ret.add_argument("--top-k", type=int, default=5, metavar="N", help="Number of results (default 5)")

    args = parser.parse_args()

    if args.cmd == "push":
        doc_id = push(json.loads(args.doc))
        print(f"Pushed: {doc_id}")

    elif args.cmd == "retrieve":
        results = retrieve(args.query, top_k=args.top_k)
        if not results:
            print("No results found.")
            return
        for r in results:
            print(f"\n[{r.get('score', 0):.4f}] {r.get('title', str(r.get('_id', '')))}")
            if "description" in r:
                print(f"  {r['description']}")
            if "content" in r:
                print(f"  {r['content'][:120]}...")


if __name__ == "__main__":
    main()
