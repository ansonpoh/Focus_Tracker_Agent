from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from database import FocusDatabase, count_switches


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"

    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_minutes(seconds: int) -> str:
    minutes = max(0, int(round(seconds / 60)))
    if minutes == 1:
        return "1 minute"
    return f"{minutes} minutes"


def _session_label(session_rows: list[dict[str, Any]]) -> str:
    if not session_rows:
        return "unknown"

    app_totals: Counter[str] = Counter()
    for row in session_rows:
        app_totals[str(row.get("app_name") or "unknown")] += int(row.get("duration_seconds") or 0)

    return app_totals.most_common(1)[0][0]


def longest_productive_session(rows: list[dict[str, Any]]) -> tuple[int, str]:
    best_duration = 0
    best_rows: list[dict[str, Any]] = []

    current_rows: list[dict[str, Any]] = []
    current_duration = 0
    previous_timestamp: datetime | None = None

    for row in rows:
        category = str(row.get("category") or "").lower()
        timestamp_value = row.get("timestamp")
        if category != "productive" or not isinstance(timestamp_value, str):
            if current_duration > best_duration:
                best_duration = current_duration
                best_rows = current_rows[:]
            current_rows = []
            current_duration = 0
            previous_timestamp = None
            continue

        current_timestamp = datetime.fromisoformat(timestamp_value)
        if previous_timestamp is not None:
            gap = (current_timestamp - previous_timestamp).total_seconds()
            if gap > 15:
                if current_duration > best_duration:
                    best_duration = current_duration
                    best_rows = current_rows[:]
                current_rows = []
                current_duration = 0

        current_rows.append(row)
        current_duration += int(row.get("duration_seconds") or 0)
        previous_timestamp = current_timestamp

    if current_duration > best_duration:
        best_duration = current_duration
        best_rows = current_rows[:]

    return best_duration, _session_label(best_rows)


def recommendation_for_day(
    *,
    total_seconds: int,
    productive_seconds: int,
    distracting_seconds: int,
    switch_count: int,
    longest_session_seconds: int,
) -> str:
    if total_seconds == 0:
        return "No activity was captured today. Start the tracker earlier tomorrow to get a useful baseline."

    if distracting_seconds >= productive_seconds and distracting_seconds >= 20 * 60:
        return "Distraction time was high. Block the main distractors during your next work block."

    if switch_count >= 20:
        return "Frequent switching suggests fragmented attention. Try a single-task block with notifications muted."

    if longest_session_seconds >= 45 * 60:
        return "You can sustain long focus sessions. Protect that stretch by scheduling deep work first."

    if productive_seconds > 0 and productive_seconds >= distracting_seconds:
        return "Productive time led the day. Keep the same environment and start with your strongest app tomorrow."

    if productive_seconds == 0 and distracting_seconds == 0:
        return "The day was mostly neutral or unknown. Label the most important apps more clearly to improve the report."

    return "Try reducing context switching and reserving one uninterrupted block for your most important task."


class DailyReporter:
    def __init__(self, database: FocusDatabase, reports_dir: Path) -> None:
        self.database = database
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_daily_report(self, report_date: str | None = None) -> Path:
        date_value = report_date or datetime.now().date().isoformat()
        rows = self.database.query_activity_for_date(date_value)
        totals = self.database.get_daily_totals(date_value)
        top_apps = self.database.get_top_apps(date_value)
        top_titles = self.database.get_top_distracting_titles(date_value)
        switch_count = count_switches(rows)
        longest_session_seconds, longest_session_app = longest_productive_session(rows)
        recommendation = recommendation_for_day(
            total_seconds=totals["total_seconds"],
            productive_seconds=totals["productive_seconds"],
            distracting_seconds=totals["distracting_seconds"],
            switch_count=switch_count,
            longest_session_seconds=longest_session_seconds,
        )

        report_lines = [
            f"# Focus Report - {date_value}",
            "",
            "## Summary",
            f"- Total tracked time: {format_duration(totals['total_seconds'])}",
            f"- Productive: {format_duration(totals['productive_seconds'])}",
            f"- Distracting: {format_duration(totals['distracting_seconds'])}",
            f"- Neutral: {format_duration(totals['neutral_seconds'])}",
            f"- Unknown: {format_duration(totals['unknown_seconds'])}",
            f"- App/window switches: {switch_count}",
            "",
            "## Top Apps",
        ]

        if top_apps:
            for index, row in enumerate(top_apps, start=1):
                report_lines.append(f"{index}. {row['app_name']} - {format_duration(int(row['total_seconds'] or 0))}")
        else:
            report_lines.append("No activity recorded.")

        report_lines.extend(["", "## Distractions"])
        if top_titles:
            for index, row in enumerate(top_titles, start=1):
                report_lines.append(f"{index}. {row['title']} - {format_duration(int(row['total_seconds'] or 0))}")
        else:
            report_lines.append("No distracting sessions recorded.")

        report_lines.extend(
            [
                "",
                "## Longest Focus Session",
            ]
        )
        if longest_session_seconds > 0:
            report_lines.append(f"{format_minutes(longest_session_seconds)} on {longest_session_app}")
        else:
            report_lines.append("No productive session recorded.")

        report_lines.extend(
            [
                "",
                "## Recommendation",
                recommendation,
            ]
        )

        report_text = "\n".join(report_lines).strip() + "\n"
        report_path = self.reports_dir / f"focus-report-{date_value}.md"
        report_path.write_text(report_text, encoding="utf-8")

        self.database.upsert_daily_summary(
            date=date_value,
            productive_seconds=totals["productive_seconds"],
            distracting_seconds=totals["distracting_seconds"],
            neutral_seconds=totals["neutral_seconds"],
            unknown_seconds=totals["unknown_seconds"],
            summary_text=recommendation,
        )

        return report_path
