"""Atomic local JSON registry for active, candidate, rejected, and historical skills."""
from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .macro_skill import migrate_macro, validate_macro


DEFAULT_SKILLS = Path(__file__).resolve().parent.parent / "database" / "skills"


class LocalSkillRegistry:
    def __init__(self, root: str | Path = DEFAULT_SKILLS):
        self.root = Path(root)
        self.store = self.root / "registry"
        self.index_path = self.store / "index.json"

    def _index(self) -> dict:
        if not self.index_path.exists():
            return {"skills": {}}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    @staticmethod
    def _atomic_json(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def _version_path(self, name: str, version: int) -> Path:
        return self.store / name / f"v{version}.json"

    def _source_path(self, name: str) -> Path:
        direct = self.root / f"{name}.macro.json"
        if direct.exists():
            return direct
        for path in self.root.glob("*.macro.json"):
            try:
                if json.loads(path.read_text(encoding="utf-8")).get("name") == name:
                    return path
            except (OSError, json.JSONDecodeError):
                continue
        return direct

    def load_skill(self, name: str, version: int | None = None) -> dict:
        index = self._index()
        entry = index["skills"].get(name, {})
        selected = version if version is not None else entry.get("active_version")
        if selected is not None and self._version_path(name, int(selected)).exists():
            return migrate_macro(json.loads(self._version_path(name, int(selected)).read_text(encoding="utf-8")))
        legacy = self._source_path(name)
        if not legacy.exists():
            raise FileNotFoundError(f"skill not found: {name}")
        return migrate_macro(json.loads(legacy.read_text(encoding="utf-8")))

    def create_candidate(self, skill: dict, reason: str = "repair") -> dict:
        candidate = copy.deepcopy(skill)
        parent = int(skill.get("version", 1))
        history = self.get_history(skill["name"])
        candidate["version"] = max([parent, *[int(item["version"]) for item in history]], default=parent) + 1
        candidate["parent_version"] = parent
        candidate["status"] = "candidate"
        candidate["candidate_reason"] = reason
        candidate["created_at"] = datetime.now(timezone.utc).isoformat()
        validate_macro(candidate)
        self._atomic_json(self._version_path(candidate["name"], candidate["version"]), candidate)
        return candidate

    def promote(self, candidate: dict, validation: dict) -> dict:
        if not validation.get("success") or not validation.get("checker_passed"):
            raise ValueError("only checker-verified candidates can be promoted")
        index = self._index()
        existing_entry = index["skills"].get(candidate["name"], {})
        previous_version = existing_entry.get("active_version", candidate.get("parent_version"))
        if previous_version is not None and int(previous_version) != int(candidate["version"]):
            previous_path = self._version_path(candidate["name"], int(previous_version))
            if previous_path.exists():
                previous = json.loads(previous_path.read_text(encoding="utf-8"))
            else:
                legacy_path = self._source_path(candidate["name"])
                previous = migrate_macro(json.loads(legacy_path.read_text(encoding="utf-8"))) \
                    if legacy_path.exists() else None
            if previous is not None:
                previous["status"] = "superseded"
                previous["superseded_by"] = candidate["version"]
                self._atomic_json(previous_path, previous)

        promoted = copy.deepcopy(candidate)
        promoted["status"] = "active"
        promoted["validation"] = validation
        promoted["promoted_at"] = datetime.now(timezone.utc).isoformat()
        stats = promoted.setdefault("stats", {})
        previous_uses = int(stats.get("uses", 0))
        stats["uses"] = previous_uses + 1
        stats["successes"] = int(stats.get("successes", 0)) + 1
        stats["failures"] = int(stats.get("failures", 0))
        stats["success_rate"] = stats["successes"] / stats["uses"]
        previous_average = float(stats.get("avg_duration", 0.0))
        stats["avg_duration"] = round(
            (previous_average * previous_uses + float(validation.get("elapsed_s", 0.0))) / stats["uses"], 3
        )
        stats["model_calls"] = int(stats.get("model_calls", 0)) + int(validation.get("model_calls", 0))
        self._atomic_json(self._version_path(promoted["name"], promoted["version"]), promoted)
        entry = index["skills"].setdefault(promoted["name"], {"versions": []})
        if previous_version is not None and previous_version not in entry["versions"]:
            entry["versions"].append(previous_version)
        if promoted["version"] not in entry["versions"]:
            entry["versions"].append(promoted["version"])
        entry["active_version"] = promoted["version"]
        self._atomic_json(self.index_path, index)
        return promoted

    def reject(self, candidate: dict, reason: str) -> dict:
        rejected = copy.deepcopy(candidate)
        rejected["status"] = "rejected"
        rejected["rejection_reason"] = reason
        rejected["rejected_at"] = datetime.now(timezone.utc).isoformat()
        self._atomic_json(self._version_path(rejected["name"], rejected["version"]), rejected)
        return rejected

    def get_history(self, name: str) -> list[dict]:
        folder = self.store / name
        result = []
        paths = folder.glob("v*.json") if folder.exists() else []
        for path in sorted(paths, key=lambda item: int(item.stem[1:])):
            value = json.loads(path.read_text(encoding="utf-8"))
            result.append({
                "version": value.get("version"), "status": value.get("status"),
                "parent_version": value.get("parent_version"),
                "validation": value.get("validation"), "path": str(path),
            })
        legacy = self._source_path(name)
        if legacy.exists() and not any(int(item["version"]) == 1 for item in result):
            value = migrate_macro(json.loads(legacy.read_text(encoding="utf-8")))
            result.insert(0, {
                "version": value.get("version", 1), "status": value.get("status", "active"),
                "parent_version": value.get("parent_version"), "validation": None,
                "path": str(legacy),
            })
        return result

    def record_run(self, skill: dict, result: dict) -> dict:
        """Persist aggregate metrics without changing the selected active version."""
        recorded = copy.deepcopy(skill)
        stats = recorded.setdefault("stats", {})
        previous_uses = int(stats.get("uses", 0))
        stats["uses"] = previous_uses + 1
        stats["successes"] = int(stats.get("successes", 0)) + int(bool(result.get("success")))
        stats["failures"] = int(stats.get("failures", 0)) + int(not bool(result.get("success")))
        stats["success_rate"] = stats["successes"] / stats["uses"]
        old_average = float(stats.get("avg_duration", 0.0))
        stats["avg_duration"] = round(
            (old_average * previous_uses + float(result.get("elapsed_s", 0.0))) / stats["uses"], 3
        )
        stats["model_calls"] = int(stats.get("model_calls", 0)) + int(result.get("model_calls", 0))
        recorded["last_run"] = {
            "success": bool(result.get("success")), "failed_step_id": result.get("failed_step_id"),
            "elapsed_s": result.get("elapsed_s"), "model_calls": result.get("model_calls", 0),
        }
        self._atomic_json(self._version_path(recorded["name"], recorded.get("version", 1)), recorded)
        index = self._index()
        entry = index["skills"].setdefault(recorded["name"], {"versions": []})
        if recorded.get("version", 1) not in entry["versions"]:
            entry["versions"].append(recorded.get("version", 1))
        entry.setdefault("active_version", recorded.get("version", 1))
        self._atomic_json(self.index_path, index)
        return recorded


def load_skill(name: str, version: int | None = None, root: str | Path = DEFAULT_SKILLS) -> dict:
    return LocalSkillRegistry(root).load_skill(name, version)


def get_history(name: str, root: str | Path = DEFAULT_SKILLS) -> list[dict]:
    return LocalSkillRegistry(root).get_history(name)
