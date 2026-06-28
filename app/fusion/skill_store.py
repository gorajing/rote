"""Fusion-native skill memory — persists and PROMOTES compiled FusedSkills across runs.

Reuses ikjun's registry on-disk conventions (database/skills/registry/<name>/, versioned files,
active_version, supersede, success-gated promotion) so fusion skills and his keyboard macros
share one skill-memory tree. Browser fusion skills are crop-gated (a `crop_b64` per spatial step)
and so are NOT expressible in his keyboard-macro schema — they're stored here in fusion-native
JSON under their OWN `fusion_index.json`, so his `validate_macro`/`LocalSkillRegistry` code never
tries to read a fusion skill as a macro.

This is the cross-run "it remembers" layer: a checker-verified recompile is promoted to a new
active version, so the next run loads the improved skill instead of recompiling again.

Success-gated, exactly like his `promote()`: only a verified skill becomes `active`.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .contract import FusedSkill, Precondition, Step

_DEFAULT_ROOT = Path(__file__).resolve().parent.parent.parent / "database" / "skills" / "registry"


def _skill_to_dict(skill: FusedSkill) -> dict:
    return {
        "engine": "fusion",
        "name": skill.name,
        "surface": skill.surface,
        "target": skill.target,
        "params": dict(skill.params),
        "verify": dict(skill.verify),
        "version": skill.version,
        "steps": [
            {
                "intent": s.intent,
                "primitive": s.primitive,
                "args": dict(s.args),
                "pre": ({"crop_b64": s.pre.crop_b64, "settle": s.pre.settle} if s.pre else None),
            }
            for s in skill.steps
        ],
    }


def _dict_to_skill(d: dict) -> FusedSkill:
    steps = []
    for s in d.get("steps", []):
        pre = s.get("pre")
        steps.append(Step(
            intent=s["intent"], primitive=s["primitive"], args=dict(s.get("args", {})),
            pre=(Precondition(crop_b64=pre.get("crop_b64"), settle=pre.get("settle", True))
                 if pre else None),
        ))
    return FusedSkill(
        name=d["name"], surface=d.get("surface", "browser"), target=d.get("target", ""),
        params=dict(d.get("params", {})), steps=steps, verify=dict(d.get("verify", {})),
        version=int(d.get("version", 1)),
    )


class FusionSkillStore:
    """Versioned, success-gated persistence for FusedSkills, in the shared registry tree."""

    def __init__(self, root: str | Path = _DEFAULT_ROOT):
        self.store = Path(root)
        self.index_path = self.store / "fusion_index.json"

    # ── internals ────────────────────────────────────────────────────────────────────────
    def _index(self) -> dict:
        if not self.index_path.exists():
            return {"skills": {}}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    @staticmethod
    def _atomic(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(value, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _vpath(self, name: str, version: int) -> Path:
        return self.store / name / f"v{version}.fusion.json"

    # ── public API ───────────────────────────────────────────────────────────────────────
    def history(self, name: str) -> list[dict]:
        folder = self.store / name
        out = []
        if folder.exists():
            for p in sorted(folder.glob("v*.fusion.json"),
                            key=lambda x: int(x.name.split(".")[0][1:])):
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append({"version": int(d["version"]), "status": d.get("status"),
                            "verified": d.get("validation", {}).get("verified")})
        return out

    def save_promoted(self, skill: FusedSkill, *, verified: bool, cu_calls: int = 0,
                      elapsed_s: float = 0.0, reason: str = "recompile") -> dict:
        """Persist a checker-verified FusedSkill as the new ACTIVE version (success-gated like
        ikjun's promote: only verified skills are promoted). Bumps the version, supersedes the
        previous active, updates the fusion index. Returns the stored record. Raises on unverified."""
        if not verified:
            raise ValueError("save_promoted is success-gated: refuse to promote an unverified skill")
        idx = self._index()
        entry = idx["skills"].setdefault(skill.name, {"versions": [], "active_version": None})
        hist = [h["version"] for h in self.history(skill.name)]
        next_v = max([skill.version, *hist], default=skill.version) + 1

        record = _skill_to_dict(skill)
        record.update({
            "version": next_v,
            "parent_version": skill.version,
            "status": "active",
            "candidate_reason": reason,
            "validation": {"verified": True, "cu_calls": int(cu_calls), "elapsed_s": float(elapsed_s)},
            "promoted_at": datetime.now(timezone.utc).isoformat(),
        })

        prev = entry.get("active_version")
        if prev is not None and self._vpath(skill.name, prev).exists():
            pd = json.loads(self._vpath(skill.name, prev).read_text(encoding="utf-8"))
            pd["status"] = "superseded"
            pd["superseded_by"] = next_v
            self._atomic(self._vpath(skill.name, prev), pd)

        self._atomic(self._vpath(skill.name, next_v), record)
        if prev is not None and prev not in entry["versions"]:
            entry["versions"].append(prev)
        if next_v not in entry["versions"]:
            entry["versions"].append(next_v)
        entry["active_version"] = next_v
        self._atomic(self.index_path, idx)
        return record

    def load_active(self, name: str) -> FusedSkill | None:
        """Return the promoted (active) FusedSkill for `name`, or None if nothing is stored."""
        entry = self._index()["skills"].get(name)
        if not entry or entry.get("active_version") is None:
            return None
        p = self._vpath(name, entry["active_version"])
        if not p.exists():
            return None
        return _dict_to_skill(json.loads(p.read_text(encoding="utf-8")))
