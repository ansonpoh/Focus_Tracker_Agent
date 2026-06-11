from __future__ import annotations

import argparse
import json
from pathlib import Path

from main import DATABASE_PATH
from database import FocusDatabase


def _parse_tags(raw_tags: str) -> list[str]:
    return [segment.strip().lower() for segment in raw_tags.split(",") if segment.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review and override dynamic app classifications.")
    parser.add_argument("--db-path", default=str(DATABASE_PATH), help="Path to the focus tracker SQLite database.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-review", help="List provisional or low-confidence classifications.")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--confidence-threshold", type=float, default=0.75)

    override_parser = subparsers.add_parser("override", help="Save an override for app/site/fingerprint classification.")
    override_parser.add_argument("--scope", choices=["app", "site", "fingerprint"], required=True)
    override_parser.add_argument("--key", required=True)
    override_parser.add_argument("--category", choices=["productive", "neutral", "distracting", "unknown"], required=True)
    override_parser.add_argument("--tags", default="")
    override_parser.add_argument("--reason", default="Manual override")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    database = FocusDatabase(Path(args.db_path))
    database.initialize()

    if args.command == "list-review":
        rows = database.list_classification_review_candidates(
            limit=int(args.limit),
            confidence_threshold=float(args.confidence_threshold),
        )
        print(json.dumps(rows, indent=2))
        return 0

    if args.command == "override":
        database.upsert_classification_override(
            scope=str(args.scope),
            key=str(args.key).strip().lower(),
            category=str(args.category),
            context_tags=_parse_tags(str(args.tags)),
            reason=str(args.reason),
        )
        print("override_saved")
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
