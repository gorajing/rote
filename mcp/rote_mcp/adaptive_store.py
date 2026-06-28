"""Persistent, searchable CU-backed skills for traces that cannot become deterministic macros."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .trace_memory import intent_hash


DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "database" / "skills" / "registry" / "adaptive"


class AdaptiveSkillStore:
    def __init__(self, root: str | Path = DEFAULT_ROOT):
        self.root = Path(root)
        self.index_path = self.root / "index.json"

    def _index(self) -> dict:
        return json.loads(self.index_path.read_text(encoding="utf-8")) if self.index_path.exists() else {"skills": {}}

    @staticmethod
    def _write(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def save(self, trace_document: dict, promotion: dict) -> dict:
        name = "adaptive_" + intent_hash(trace_document["intent"])[:16]
        index = self._index()
        entry = index["skills"].setdefault(name, {"versions": [], "active_version": None})
        version = max(entry["versions"], default=0) + 1
        record = {
            "name": name, "version": version, "engine": "adaptive", "status": "active",
            "surface": trace_document.get("surface", "desktop"),
            "description": trace_document["description"], "intent": trace_document["intent"],
            "variables": trace_document.get("variables", {}), "steps": trace_document.get("steps", []),
            "source_trace_id": trace_document.get("trace_id"), "verified": True,
            "verification_mode": "adaptive_cu",
            "checker": {"type": "adaptive_cu"},
            "promotion_failure": promotion,
        }
        self._write(self.root / name / f"v{version}.json", record)
        entry["versions"].append(version)
        entry["active_version"] = version
        self._write(self.index_path, index)
        return record

    def load_version(self, name: str, version: int) -> dict | None:
        index = self._index().get("skills", {}).get(name)
        if not index or int(index.get("active_version", -1)) != int(version):
            return None
        path = self.root / name / f"v{version}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def list_active(self) -> list[dict]:
        result = []
        for name, entry in self._index().get("skills", {}).items():
            record = self.load_version(name, entry["active_version"])
            if record:
                result.append(record)
        return result

    def history(self, name: str) -> list[dict]:
        entry = self._index().get("skills", {}).get(name, {})
        return [{"version": version, "status": "active" if version == entry.get("active_version") else "superseded",
                 "verified": True, "verification_mode": "adaptive_cu"}
                for version in entry.get("versions", [])]
