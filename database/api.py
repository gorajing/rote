#!/usr/bin/env python3
"""
Rote Database API — load and query Skill objects stored as JSON.

CLI:
    python database/api.py web
    python database/api.py web --address amazon
    python database/api.py web --purpose buy --skill
    python database/api.py adobe --purpose generate

Python:
    from database.api import query
    skills = query(platform="web", address="amazon", load_skill=True)
"""
import json
import argparse
import sys
from pathlib import Path
from typing import Optional

# Allow running as `python database/api.py` from the repo root.
sys.path.insert(0, str(Path(__file__).parent.parent))
from app.schemas import Skill  # noqa: E402

_DB_DIR = Path(__file__).parent
_INDEX_PATH = _DB_DIR / "index.json"
_DATA_DIR = _DB_DIR / "data"

VALID_PLATFORMS = {"web", "excel", "adobe", "apple_email", "whatsapp"}
VALID_PURPOSES = {"buy", "generate", "communicate"}


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
    """
    Search the index and return matching entries.

    Args:
        platform:   Required. One of: web, excel, adobe, apple_email, whatsapp.
        address:    Optional. Site identifier (amazon, ebay, facebook, youtube, booking …).
                    Meaningful only when platform is "web".
        purpose:    Optional. One of: buy, generate, communicate.
        load_skill: If True, each result is a fully deserialised Skill dataclass.
                    If False, each result is a flat dict of index metadata.

    Returns:
        List of index-metadata dicts, or Skill objects when load_skill=True.

    Raises:
        ValueError: on invalid platform or purpose values.
    """
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


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_skill(skill: Skill) -> None:
    print(f"  name:           {skill.name}")
    print(f"  site:           {skill.site}")
    print(f"  goal:           {skill.goal_template}")
    print(f"  params:         {[p['name'] for p in skill.params]}")
    print(f"  steps:          {len(skill.steps)}")
    print(f"  status:         {skill.status}  (v{skill.version})")
    stats = skill.stats
    print(
        f"  stats:          {stats['uses']} uses | "
        f"{stats['successes']} ok | {stats['failures']} fail | "
        f"{stats['success_rate']:.0%} rate"
    )


def _print_entry(entry: dict) -> None:
    print(f"  id:             {entry['id']}")
    print(f"  skill:          {entry['skill_name']}")
    print(f"  platform:       {entry['platform']}")
    if entry.get("address"):
        print(f"  address:        {entry['address']}")
    print(f"  purpose:        {entry['purpose']}")
    print(f"  date:           {entry['date']}")
    print(f"  last_validation:{entry['last_validation']}")
    print(f"  validations:    {entry['validations_count']}")
    print(f"  status:         {entry['status']}  (v{entry['version']})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Query the Rote Skill database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python database/api.py web\n"
            "  python database/api.py web --address amazon\n"
            "  python database/api.py web --purpose buy --skill\n"
            "  python database/api.py adobe --purpose generate\n"
        ),
    )
    parser.add_argument(
        "platform",
        help=f"Required. One of: {', '.join(sorted(VALID_PLATFORMS))}",
    )
    parser.add_argument("--address", metavar="SITE", help="Site filter (e.g. amazon, ebay, facebook).")
    parser.add_argument("--purpose", choices=sorted(VALID_PURPOSES), help="Purpose filter.")
    parser.add_argument(
        "--skill", action="store_true",
        help="Deserialise and print full Skill objects instead of index metadata.",
    )
    args = parser.parse_args()

    try:
        results = query(
            platform=args.platform,
            address=args.address,
            purpose=args.purpose,
            load_skill=args.skill,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if not results:
        print("No matching entries found.")
        return

    noun = "Skill" if args.skill else "entry"
    print(f"Found {len(results)} matching {noun}{'s' if len(results) != 1 else ''}:\n")
    for item in results:
        if isinstance(item, Skill):
            _print_skill(item)
        else:
            _print_entry(item)
        print()


if __name__ == "__main__":
    main()
