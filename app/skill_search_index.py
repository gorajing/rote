"""Project executable local macros into MongoDB Atlas search documents."""
from __future__ import annotations

import argparse
import json

from database.api import push_many

from .local_skill_registry import LocalSkillRegistry


def skill_document(skill: dict) -> dict | None:
    """Build an Atlas descriptor, excluding skills that are unsafe to execute."""
    if skill.get("surface", "desktop") != "desktop":
        return None
    if skill.get("status", "active") != "active" or not skill.get("checker"):
        return None
    name = skill["name"]
    if name.startswith("stale_"):
        return None
    version = int(skill.get("version", 1))
    app = skill.get("app") or "macOS"
    note = skill.get("note") or f"Run the verified {name.replace('_', ' ')} workflow in {app}."
    return {
        "_id": f"macro:{name}:v{version}",
        "doc_type": "executable_skill",
        "skill_name": name,
        "version": version,
        "surface": "desktop",
        "app": app,
        "status": "active",
        "checker_verified": True,
        "description": note,
        "params": list(skill.get("params", {}).keys()),
    }


def build_documents(registry: LocalSkillRegistry | None = None) -> list[dict]:
    registry = registry or LocalSkillRegistry()
    documents = []
    for summary in registry.list_skills(surface="desktop"):
        skill = registry.load_skill(summary["name"], summary["version"])
        document = skill_document(skill)
        if document is not None:
            documents.append(document)
    return documents


def sync_index(registry: LocalSkillRegistry | None = None) -> list[str]:
    documents = build_documents(registry)
    return push_many(documents) if documents else []


def main() -> None:
    parser = argparse.ArgumentParser(description="Index active Rote desktop macros in Atlas")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    documents = build_documents()
    if args.dry_run:
        print(json.dumps(documents, indent=2))
        return
    print(json.dumps({"indexed": len(sync_index()), "documents": len(documents)}))


if __name__ == "__main__":
    main()
