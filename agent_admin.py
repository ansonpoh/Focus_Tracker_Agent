from __future__ import annotations

import argparse
import json
from pathlib import Path

from database import FocusDatabase
from main import DATABASE_PATH


def _parse_days(raw_value: str) -> list[int]:
    return [int(item.strip()) for item in raw_value.split(",") if item.strip()]


def _parse_csv(raw_value: str) -> list[str]:
    return [item.strip().lower() for item in raw_value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage adaptive coach goals and intervention stats.")
    parser.add_argument("--db-path", default=str(DATABASE_PATH), help="Path to the focus tracker SQLite database.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-goals", help="List configured goals.")

    add_goal = subparsers.add_parser("add-goal", help="Add a goal.")
    add_goal.add_argument("--type", choices=["daily_productive_minutes", "distracting_limit", "focus_block_count"], required=True)
    add_goal.add_argument("--name", required=True)
    add_goal.add_argument("--target", type=int, required=True)
    add_goal.add_argument("--window-minutes", type=int, default=0)
    add_goal.add_argument("--start", default="08:00")
    add_goal.add_argument("--end", default="18:00")
    add_goal.add_argument("--days", default="0,1,2,3,4")
    add_goal.add_argument("--blocked-sites", default="")
    add_goal.add_argument("--blocked-apps", default="")

    disable_goal = subparsers.add_parser("disable-goal", help="Disable a goal.")
    disable_goal.add_argument("--id", type=int, required=True)

    subparsers.add_parser("list-intervention-stats", help="Summarize intervention effectiveness.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    database = FocusDatabase(Path(args.db_path))
    database.initialize()

    if args.command == "list-goals":
        print(json.dumps(database.list_goals(), indent=2))
        return 0

    if args.command == "add-goal":
        goal_id = database.upsert_goal(
            goal_type=str(args.type),
            name=str(args.name),
            target_value=int(args.target),
            window_minutes=int(args.window_minutes),
            schedule_start=str(args.start),
            schedule_end=str(args.end),
            days_of_week=_parse_days(str(args.days)),
            config={
                "blocked_sites": _parse_csv(str(args.blocked_sites)),
                "blocked_apps": _parse_csv(str(args.blocked_apps)),
            },
        )
        print(json.dumps({"goal_id": goal_id}))
        return 0

    if args.command == "disable-goal":
        database.disable_goal(int(args.id))
        print("goal_disabled")
        return 0

    if args.command == "list-intervention-stats":
        print(json.dumps(database.get_intervention_effectiveness_stats(), indent=2))
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
