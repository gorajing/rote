#!/usr/bin/env python3
"""
Rote Database API — query agent instruction files by metadata.

Usage (CLI):
    python api.py web
    python api.py web --address amazon
    python api.py web --purpose buy
    python api.py web --address facebook --purpose communicate --content

Usage (Python):
    from database.api import query
    results = query(platform="web", address="amazon", include_content=True)
"""
import json
import argparse
from pathlib import Path
from typing import Optional

_DB_DIR = Path(__file__).parent
_INDEX_PATH = _DB_DIR / "index.json"
_DATA_DIR = _DB_DIR / "data"

VALID_PLATFORMS = {"web", "excel", "adobe", "apple_email", "whatsapp"}
VALID_PURPOSES = {"buy", "generate", "communicate"}


def _load_index() -> list[dict]:
    with open(_INDEX_PATH, encoding="utf-8") as f:
        return json.load(f)["entries"]


def query(
    platform: str,
    address: Optional[str] = None,
    purpose: Optional[str] = None,
    include_content: bool = False,
) -> list[dict]:
    """
    Search the index and return matching entries.

    Args:
        platform:        Required. One of: web, excel, adobe, apple_email, whatsapp.
        address:         Optional. Website identifier (e.g. amazon, ebay, facebook, youtube, booking).
                         Only meaningful when platform is "web".
        purpose:         Optional. One of: buy, generate, communicate.
        include_content: If True, each result includes a "content" key with the raw instruction text.

    Returns:
        List of matching entry dicts. Each dict contains all index metadata;
        if include_content is True, a "content" key is added.

    Raises:
        ValueError: if platform or purpose are not valid known values.
    """
    if platform not in VALID_PLATFORMS:
        raise ValueError(f"Invalid platform '{platform}'. Must be one of: {', '.join(sorted(VALID_PLATFORMS))}")
    if purpose is not None and purpose not in VALID_PURPOSES:
        raise ValueError(f"Invalid purpose '{purpose}'. Must be one of: {', '.join(sorted(VALID_PURPOSES))}")

    results = []
    for entry in _load_index():
        if entry["platform"] != platform:
            continue
        if address is not None and entry.get("address") != address:
            continue
        if purpose is not None and entry["purpose"] != purpose:
            continue

        result = dict(entry)
        if include_content:
            result["content"] = (_DATA_DIR / entry["filename"]).read_text(encoding="utf-8")

        results.append(result)

    return results


def _print_results(results: list[dict]) -> None:
    if not results:
        print("No matching entries found.")
        return

    print(f"Found {len(results)} matching entr{'y' if len(results) == 1 else 'ies'}:\n")
    for entry in results:
        print(f"  ID:               {entry['id']}")
        print(f"  File:             {entry['filename']}")
        print(f"  Title:            {entry['title']}")
        print(f"  Platform:         {entry['platform']}")
        if entry.get("address"):
            print(f"  Address:          {entry['address']}")
        print(f"  Purpose:          {entry['purpose']}")
        print(f"  Date:             {entry['date']}")
        print(f"  Last validation:  {entry['last_validation']}")
        print(f"  Validations:      {entry['validations_count']}")
        if "content" in entry:
            print(f"\n  {'─' * 60}")
            print(entry["content"])
            print(f"  {'─' * 60}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Query the Rote agent instruction database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python api.py web\n"
            "  python api.py web --address amazon\n"
            "  python api.py web --purpose buy --content\n"
            "  python api.py adobe --purpose generate\n"
        ),
    )
    parser.add_argument(
        "platform",
        help=f"Platform to filter on (required). One of: {', '.join(sorted(VALID_PLATFORMS))}",
    )
    parser.add_argument(
        "--address",
        metavar="SITE",
        help="Website address to filter on (e.g. amazon, ebay, facebook, youtube, booking).",
    )
    parser.add_argument(
        "--purpose",
        choices=sorted(VALID_PURPOSES),
        help="Purpose to filter on.",
    )
    parser.add_argument(
        "--content",
        action="store_true",
        help="Include the full instruction text in the output.",
    )

    args = parser.parse_args()

    try:
        results = query(
            platform=args.platform,
            address=args.address,
            purpose=args.purpose,
            include_content=args.content,
        )
    except ValueError as exc:
        parser.error(str(exc))

    _print_results(results)


if __name__ == "__main__":
    main()
