"""HTTP gateway exposing the unchanged database.api push/retrieve contract to MCP."""
from __future__ import annotations

import os
import json

from flask import Flask, jsonify, request

from database.api import push, retrieve


def _json_safe(value):
    return json.loads(json.dumps(value, default=str))


def create_app(push_fn=push, retrieve_fn=retrieve) -> Flask:
    app = Flask(__name__)

    @app.get("/v1/health")
    def health():
        return jsonify({"ok": True, "service": "rote-database-gateway"})

    @app.post("/v1/documents")
    def documents():
        payload = request.get_json(silent=True) or {}
        document = payload.get("document")
        if not isinstance(document, dict):
            return jsonify({"ok": False, "error": "document must be an object"}), 400
        try:
            return jsonify({"ok": True, "id": push_fn(document)})
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 503

    @app.post("/v1/search")
    def search():
        payload = request.get_json(silent=True) or {}
        query = payload.get("query")
        top_k = payload.get("top_k", 5)
        filters = payload.get("filters")
        if not isinstance(query, str) or not query.strip():
            return jsonify({"ok": False, "error": "query must be non-empty"}), 400
        if not isinstance(top_k, int) or not 1 <= top_k <= 50:
            return jsonify({"ok": False, "error": "top_k must be between 1 and 50"}), 400
        if filters is not None and not isinstance(filters, dict):
            return jsonify({"ok": False, "error": "filters must be an object"}), 400
        try:
            return jsonify({"ok": True, "results": _json_safe(
                retrieve_fn(query, top_k=top_k, filters=filters)
            )})
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 503

    return app


def main() -> None:
    create_app().run(
        host=os.getenv("ROTE_DATABASE_API_HOST", "127.0.0.1"),
        port=int(os.getenv("ROTE_DATABASE_API_PORT", "8810")),
        debug=False,
    )


if __name__ == "__main__":
    main()
