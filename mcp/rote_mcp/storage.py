"""HTTP-only MCP client for the database gateway."""
from __future__ import annotations

import os

from google import genai
from google.genai import types
import requests


_MODEL = "gemini-embedding-2"
_API_URL = os.getenv("ROTE_DATABASE_API_URL", "http://127.0.0.1:8810").rstrip("/")
_client = None


def _genai():
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


def _embed(text: str, task_type: str) -> list[float]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("embedding text must be non-empty")
    response = _genai().models.embed_content(
        model=_MODEL, contents=text,
        config=types.EmbedContentConfig(taskType=task_type, outputDimensionality=3072),
    )
    return list(response.embeddings[0].values)


def embed_query(text: str) -> list[float]:
    return _embed(text, "RETRIEVAL_QUERY")


def prepare_document(document: dict) -> dict:
    text = document.get("search_text") or document.get("description")
    return {
        **{key: value for key, value in document.items() if key != "_id"},
        "embedding_model": _MODEL,
        "embedding": _embed(text, "RETRIEVAL_DOCUMENT"),
    }


def push_document(document: dict) -> str:
    response = requests.post(f"{_API_URL}/v1/documents", json={"document": document}, timeout=15)
    response.raise_for_status()
    return str(response.json()["id"])


def _retrieve(search_text: str, top_k: int, filters: dict) -> list[dict]:
    response = requests.post(
        f"{_API_URL}/v1/search",
        json={"query": search_text, "top_k": top_k, "filters": filters}, timeout=20,
    )
    response.raise_for_status()
    value = response.json()
    return value.get("results", [])


def retrieve_skill_documents(search_text: str, top_k: int = 5, surface: str | None = None) -> list[dict]:
    filters = {
        "doc_type": "executable_skill", "status": "active",
        "surface": surface,
    }
    return _retrieve(search_text, top_k, filters)


def push_trace(document: dict) -> str:
    return push_document(document)


def retrieve_traces(search_text: str, surface: str = "desktop", top_k: int = 3) -> list[dict]:
    matches = _retrieve(search_text, min(50, max(20, top_k * 5)), filters={
        "doc_type": "execution_trace", "surface": surface,
        "completion_status": "model_completed", "hint_eligible": True, "verified": False,
    })
    newest = {}
    for match in matches:
        key = match.get("intent_hash") or str(match.get("_id"))
        current = newest.get(key)
        if current is None or str(match.get("created_at", "")) > str(current.get("created_at", "")):
            newest[key] = match
    return sorted(newest.values(), key=lambda item: float(item.get("score", 0)), reverse=True)[:top_k]


def health() -> dict:
    response = requests.get(f"{_API_URL}/v1/health", timeout=5)
    response.raise_for_status()
    return response.json()
