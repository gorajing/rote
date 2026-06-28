"""Read-only MCP adapter for the unchanged FusionSkillStore."""
from __future__ import annotations

import json

from app.fusion.skill_store import FusionSkillStore, _dict_to_skill


class FusionRegistryAdapter:
    def __init__(self, store: FusionSkillStore | None = None):
        self.store = store or FusionSkillStore()

    def load_active(self, name: str):
        return self.store.load_active(name)

    def history(self, name: str) -> list[dict]:
        return self.store.history(name)

    def load_record(self, name: str, version: int) -> dict | None:
        path = self.store._vpath(name, int(version))
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def load_version(self, name: str, version: int):
        record = self.load_record(name, version)
        return _dict_to_skill(record) if record else None

    def list_active(self) -> list[tuple[object, dict]]:
        result = []
        for name, entry in self.store._index().get("skills", {}).items():
            version = entry.get("active_version")
            if version is None:
                continue
            record = self.load_record(name, int(version))
            if not record or record.get("status") != "active" or not record.get("validation", {}).get("verified"):
                continue
            result.append((_dict_to_skill(record), record))
        return result
