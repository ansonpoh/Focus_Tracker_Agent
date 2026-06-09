from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
import json

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


def _parse_context_tags(row: dict[str, Any]) -> list[str]:
    raw_value = row.get("context_tags")
    if isinstance(raw_value, list):
        return [str(item).strip().lower() for item in raw_value if str(item).strip()]
    if isinstance(raw_value, str):
        try:
            decoded = json.loads(raw_value)
        except json.JSONDecodeError:
            decoded = [segment.strip() for segment in raw_value.split(",")]
        if isinstance(decoded, list):
            return [str(item).strip().lower() for item in decoded if str(item).strip()]
    return []


def _row_end(timestamp_value: str, duration_seconds: int) -> datetime:
    return datetime.fromisoformat(timestamp_value) + timedelta(seconds=max(0, duration_seconds))


@dataclass
class FocusBlock:
    start: datetime
    end: datetime
    duration_seconds: int
    primary_app: str
    context_tags: list[str]


@dataclass
class DaySnapshot:
    date_value: date
    totals: dict[str, int]
    switch_count: int
    focus_block_count: int
    longest_focus_seconds: int


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


def build_focus_blocks(rows: list[dict[str, Any]], *, max_gap_seconds: int = 15) -> list[FocusBlock]:
    blocks: list[FocusBlock] = []
    current_rows: list[dict[str, Any]] = []
    previous_end: datetime | None = None

    for row in rows:
        category = str(row.get("category") or "").lower()
        timestamp_value = row.get("timestamp")
        if category != "productive" or not isinstance(timestamp_value, str):
            if current_rows:
                blocks.append(_block_from_rows(current_rows))
                current_rows = []
                previous_end = None
            continue

        row_end = _row_end(timestamp_value, int(row.get("duration_seconds") or 0))
        row_start = datetime.fromisoformat(timestamp_value)
        if previous_end is not None and (row_start - previous_end).total_seconds() > max_gap_seconds:
            blocks.append(_block_from_rows(current_rows))
            current_rows = []

        current_rows.append(row)
        previous_end = row_end

    if current_rows:
        blocks.append(_block_from_rows(current_rows))

    return blocks


def _block_from_rows(rows: list[dict[str, Any]]) -> FocusBlock:
    app_totals: Counter[str] = Counter()
    tags: list[str] = []

    for row in rows:
        app_totals[str(row.get("app_name") or "unknown")] += int(row.get("duration_seconds") or 0)
        for tag in _parse_context_tags(row):
            if tag not in tags:
                tags.append(tag)

    first_timestamp = str(rows[0]["timestamp"])
    last_row = rows[-1]
    last_timestamp = str(last_row["timestamp"])
    duration_seconds = sum(int(row.get("duration_seconds") or 0) for row in rows)
    return FocusBlock(
        start=datetime.fromisoformat(first_timestamp),
        end=_row_end(last_timestamp, int(last_row.get("duration_seconds") or 0)),
        duration_seconds=duration_seconds,
        primary_app=app_totals.most_common(1)[0][0] if app_totals else "unknown",
        context_tags=tags,
    )


def interruption_metrics(rows: list[dict[str, Any]]) -> tuple[int, list[int]]:
    interruptions = 0
    recovery_seconds: list[int] = []
    last_productive_end: datetime | None = None
    pending_recovery_start: datetime | None = None
    in_interruption = False

    for row in rows:
        timestamp_value = row.get("timestamp")
        if not isinstance(timestamp_value, str):
            continue

        row_start = datetime.fromisoformat(timestamp_value)
        row_end = _row_end(timestamp_value, int(row.get("duration_seconds") or 0))
        category = str(row.get("category") or "").lower()

        if category == "productive":
            if pending_recovery_start is not None:
                recovery_seconds.append(max(0, int((row_start - pending_recovery_start).total_seconds())))
                pending_recovery_start = None
            in_interruption = False
            last_productive_end = row_end
            continue

        if last_productive_end is None:
            continue

        if not in_interruption:
            interruptions += 1
            in_interruption = True

        if category == "distracting" and pending_recovery_start is None:
            pending_recovery_start = row_start

    return interruptions, recovery_seconds


def top_context_tags(rows: list[dict[str, Any]], limit: int = 6) -> list[tuple[str, int]]:
    totals: Counter[str] = Counter()
    for row in rows:
        duration = int(row.get("duration_seconds") or 0)
        for tag in _parse_context_tags(row):
            totals[tag] += duration

    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:limit]


def top_sites(rows: list[dict[str, Any]], limit: int = 5) -> list[tuple[str, int]]:
    totals: Counter[str] = Counter()
    for row in rows:
        site_hint = str(row.get("site_hint") or "").strip().lower()
        if site_hint:
            totals[site_hint] += int(row.get("duration_seconds") or 0)

    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:limit]


def top_distracting_sites(rows: list[dict[str, Any]], limit: int = 5) -> list[tuple[str, int]]:
    totals: Counter[str] = Counter()
    for row in rows:
        if str(row.get("category") or "").lower() != "distracting":
            continue
        site_hint = str(row.get("site_hint") or "").strip().lower()
        if site_hint:
            totals[site_hint] += int(row.get("duration_seconds") or 0)

    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:limit]


def _bar(value: int, maximum: int, width: int = 20) -> str:
    if value <= 0 or maximum <= 0:
        return ""
    filled = max(1, round((value / maximum) * width))
    return "#" * filled


def hourly_breakdown(rows: list[dict[str, Any]]) -> list[tuple[int, int, int, int, int]]:
    buckets: dict[int, dict[str, int]] = {
        hour: {"productive": 0, "distracting": 0, "neutral": 0, "unknown": 0}
        for hour in range(24)
    }
    for row in rows:
        timestamp_value = row.get("timestamp")
        if not isinstance(timestamp_value, str):
            continue
        hour = datetime.fromisoformat(timestamp_value).hour
        category = str(row.get("category") or "unknown").lower()
        duration = int(row.get("duration_seconds") or 0)
        if category not in buckets[hour]:
            category = "unknown"
        buckets[hour][category] += duration

    return [
        (
            hour,
            buckets[hour]["productive"],
            buckets[hour]["distracting"],
            buckets[hour]["neutral"],
            buckets[hour]["unknown"],
        )
        for hour in range(24)
        if sum(buckets[hour].values()) > 0
    ]


def category_distribution(totals: dict[str, int]) -> list[tuple[str, int]]:
    return [
        ("productive", totals["productive_seconds"]),
        ("distracting", totals["distracting_seconds"]),
        ("neutral", totals["neutral_seconds"]),
        ("unknown", totals["unknown_seconds"]),
    ]


def distracting_windows_over_time(rows: list[dict[str, Any]], limit: int = 3) -> list[tuple[str, list[tuple[str, int]]]]:
    title_totals: Counter[str] = Counter()
    hourly_by_title: dict[str, Counter[str]] = {}

    for row in rows:
        if str(row.get("category") or "").lower() != "distracting":
            continue
        timestamp_value = row.get("timestamp")
        if not isinstance(timestamp_value, str):
            continue
        hour_label = datetime.fromisoformat(timestamp_value).strftime("%H:00")
        title = str(row.get("window_title") or "").strip() or str(row.get("app_name") or "unknown")
        duration = int(row.get("duration_seconds") or 0)
        title_totals[title] += duration
        if title not in hourly_by_title:
            hourly_by_title[title] = Counter()
        hourly_by_title[title][hour_label] += duration

    top_titles = [title for title, _ in sorted(title_totals.items(), key=lambda item: (-item[1], item[0]))[:limit]]
    return [
        (title, sorted(hourly_by_title[title].items(), key=lambda item: item[0]))
        for title in top_titles
    ]


def best_day_this_week(database: FocusDatabase, target_date: date) -> DaySnapshot | None:
    week_start = target_date - timedelta(days=target_date.weekday())
    snapshots: list[DaySnapshot] = []
    for offset in range(7):
        current_date = week_start + timedelta(days=offset)
        rows = database.query_activity_for_date(current_date.isoformat())
        if not rows:
            continue
        totals = _calculate_totals(rows)
        longest_focus_seconds, _ = longest_productive_session(rows)
        snapshots.append(
            DaySnapshot(
                date_value=current_date,
                totals=totals,
                switch_count=count_switches(rows),
                focus_block_count=len(build_focus_blocks(rows)),
                longest_focus_seconds=longest_focus_seconds,
            )
        )

    if not snapshots:
        return None

    return max(
        snapshots,
        key=lambda item: (
            item.totals["productive_seconds"],
            item.longest_focus_seconds,
            -item.switch_count,
        ),
    )


def previous_day_snapshot(database: FocusDatabase, target_date: date) -> DaySnapshot | None:
    previous_date = target_date - timedelta(days=1)
    rows = database.query_activity_for_date(previous_date.isoformat())
    if not rows:
        return None

    totals = _calculate_totals(rows)
    longest_focus_seconds, _ = longest_productive_session(rows)
    return DaySnapshot(
        date_value=previous_date,
        totals=totals,
        switch_count=count_switches(rows),
        focus_block_count=len(build_focus_blocks(rows)),
        longest_focus_seconds=longest_focus_seconds,
    )


def _calculate_totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        "total_seconds": 0,
        "productive_seconds": 0,
        "distracting_seconds": 0,
        "neutral_seconds": 0,
        "unknown_seconds": 0,
    }

    for row in rows:
        duration = int(row.get("duration_seconds") or 0)
        category = str(row.get("category") or "unknown").lower()
        totals["total_seconds"] += duration
        if category == "productive":
            totals["productive_seconds"] += duration
        elif category == "distracting":
            totals["distracting_seconds"] += duration
        elif category == "neutral":
            totals["neutral_seconds"] += duration
        else:
            totals["unknown_seconds"] += duration

    return totals


def _delta_label(current: int, previous: int) -> str:
    delta = current - previous
    if delta == 0:
        return "no change"
    direction = "up" if delta > 0 else "down"
    return f"{direction} {format_duration(abs(delta))}"


def recommendation_for_patterns(
    *,
    totals: dict[str, int],
    switch_count: int,
    longest_session_seconds: int,
    top_distracting_site_rows: list[tuple[str, int]],
    focus_blocks: list[FocusBlock],
    interruptions: int,
    recovery_seconds: list[int],
    hourly_rows: list[tuple[int, int, int, int, int]],
    previous_day: DaySnapshot | None,
    best_week_day: DaySnapshot | None,
) -> str:
    if totals["total_seconds"] == 0:
        return "No activity was captured today. Start the tracker earlier tomorrow to get a useful baseline."

    if top_distracting_site_rows:
        top_site, top_site_seconds = top_distracting_site_rows[0]
        if totals["distracting_seconds"] > 0 and top_site_seconds >= max(15 * 60, totals["distracting_seconds"] // 2):
            return f"Most distraction time came from {top_site}. Blocking or timeboxing that site would have the biggest impact."

    if interruptions >= 4 and recovery_seconds:
        average_recovery = sum(recovery_seconds) // len(recovery_seconds)
        return (
            f"Interruptions broke momentum {interruptions} times, and recovery averaged {format_duration(average_recovery)}. "
            "Protect one uninterrupted block before opening communication or browsing tools."
        )

    if previous_day is not None:
        productive_delta = totals["productive_seconds"] - previous_day.totals["productive_seconds"]
        if productive_delta >= 30 * 60:
            return f"Productive time improved by {format_duration(productive_delta)} versus yesterday. Repeat the same opening routine tomorrow."
        if productive_delta <= -(30 * 60):
            return f"Productive time fell by {format_duration(abs(productive_delta))} versus yesterday. Recreate yesterday's strongest work block earlier in the day."

    if best_week_day is not None and best_week_day.totals["productive_seconds"] > totals["productive_seconds"]:
        gap = best_week_day.totals["productive_seconds"] - totals["productive_seconds"]
        if gap >= 30 * 60:
            return (
                f"Your best day this week was {best_week_day.date_value.isoformat()}, ahead by {format_duration(gap)} of productive time. "
                "Compare that day's first two hours with today and copy the setup that kept you in focus."
            )

    if hourly_rows:
        best_hour = max(hourly_rows, key=lambda item: item[1])
        if best_hour[1] >= 20 * 60:
            return f"Your strongest focus window started around {best_hour[0]:02d}:00. Reserve that hour for deep work before meetings or browsing."

    if longest_session_seconds >= 45 * 60 and len(focus_blocks) >= 2:
        return "You sustained at least one strong block today. Batch shallow work together and defend the longest block as a repeatable template."

    if switch_count >= 20:
        return "Frequent switching suggests fragmented attention. Try a single-task block with notifications muted."

    if totals["productive_seconds"] >= totals["distracting_seconds"]:
        return "Productive time led the day. Keep the same environment and start with your strongest app tomorrow."

    return "Try reducing context switching and reserving one uninterrupted block for your most important task."


class DailyReporter:
    def __init__(self, database: FocusDatabase, reports_dir: Path) -> None:
        self.database = database
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_daily_report(self, report_date: str | None = None) -> Path:
        date_value = report_date or datetime.now().date().isoformat()
        start = datetime.fromisoformat(f"{date_value}T00:00:00")
        end = start + timedelta(days=1)
        return self._generate_period_report(
            label=f"Focus Report - {date_value}",
            report_filename=f"focus-report-{date_value}.md",
            date_key=date_value,
            anchor_date=date.fromisoformat(date_value),
            rows=self.database.query_activity_between(start, end),
        )

    def generate_weekly_report(self, any_date: str | None = None) -> Path:
        target_date = date.fromisoformat(any_date) if any_date else datetime.now().date()
        week_start = target_date - timedelta(days=target_date.weekday())
        week_end = week_start + timedelta(days=7)
        return self._generate_period_report(
            label=f"Focus Report - Week of {week_start.isoformat()}",
            report_filename=f"focus-report-week-{week_start.isoformat()}.md",
            date_key=f"{week_start.isoformat()}_week",
            anchor_date=target_date,
            rows=self.database.query_activity_between(
                datetime.combine(week_start, datetime.min.time()),
                datetime.combine(week_end, datetime.min.time()),
            ),
        )

    def generate_monthly_report(self, any_date: str | None = None) -> Path:
        target_date = date.fromisoformat(any_date) if any_date else datetime.now().date()
        month_start = target_date.replace(day=1)
        if month_start.month == 12:
            next_month = month_start.replace(year=month_start.year + 1, month=1)
        else:
            next_month = month_start.replace(month=month_start.month + 1)

        return self._generate_period_report(
            label=f"Focus Report - {month_start.strftime('%Y-%m')}",
            report_filename=f"focus-report-month-{month_start.strftime('%Y-%m')}.md",
            date_key=f"{month_start.strftime('%Y-%m')}_month",
            anchor_date=target_date,
            rows=self.database.query_activity_between(
                datetime.combine(month_start, datetime.min.time()),
                datetime.combine(next_month, datetime.min.time()),
            ),
        )

    def _generate_period_report(
        self,
        *,
        label: str,
        report_filename: str,
        date_key: str,
        anchor_date: date,
        rows: list[dict[str, Any]],
    ) -> Path:
        totals = _calculate_totals(rows)
        top_apps = self._top_apps(rows)
        top_titles = self._top_distracting_titles(rows)
        top_site_rows = top_sites(rows)
        top_distracting_site_rows = top_distracting_sites(rows)
        tag_rows = top_context_tags(rows)
        hourly_rows = hourly_breakdown(rows)
        category_rows = category_distribution(totals)
        distracting_timeline = distracting_windows_over_time(rows)
        switch_count = count_switches(rows)
        longest_session_seconds, longest_session_app = longest_productive_session(rows)
        focus_blocks = build_focus_blocks(rows)
        interruptions, recovery_seconds = interruption_metrics(rows)
        previous_day = previous_day_snapshot(self.database, anchor_date)
        best_week_day = best_day_this_week(self.database, anchor_date)
        recommendation = recommendation_for_patterns(
            totals=totals,
            switch_count=switch_count,
            longest_session_seconds=longest_session_seconds,
            top_distracting_site_rows=top_distracting_site_rows,
            focus_blocks=focus_blocks,
            interruptions=interruptions,
            recovery_seconds=recovery_seconds,
            hourly_rows=hourly_rows,
            previous_day=previous_day,
            best_week_day=best_week_day,
        )

        report_lines = [
            f"# {label}",
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

        report_lines.extend(["", "## Browser Sites"])
        if top_site_rows:
            for index, (site_hint, total_seconds) in enumerate(top_site_rows, start=1):
                report_lines.append(f"{index}. {site_hint} - {format_duration(total_seconds)}")
        else:
            report_lines.append("No browser sites identified.")

        report_lines.extend(["", "## Context Tags"])
        if tag_rows:
            for index, (tag, total_seconds) in enumerate(tag_rows, start=1):
                report_lines.append(f"{index}. {tag} - {format_duration(total_seconds)}")
        else:
            report_lines.append("No context tags recorded.")

        report_lines.extend(["", "## Charts", "", "### Hourly Breakdown"])
        if hourly_rows:
            hourly_max = max(sum(values[1:]) for values in hourly_rows)
            for hour, productive, distracting, neutral, unknown in hourly_rows:
                total_seconds = productive + distracting + neutral + unknown
                report_lines.append(
                    f"- {hour:02d}:00 {format_duration(total_seconds)} "
                    f"[P {format_duration(productive)} { _bar(productive, hourly_max, 8) or '-' }] "
                    f"[D {format_duration(distracting)} { _bar(distracting, hourly_max, 8) or '-' }]"
                )
        else:
            report_lines.append("No hourly data recorded.")

        report_lines.extend(["", "### Category Distribution"])
        category_max = max((value for _, value in category_rows), default=0)
        for category_name, value in category_rows:
            report_lines.append(f"- {category_name}: {format_duration(value)} {_bar(value, category_max)}")

        report_lines.extend(["", "### Top Distracting Windows Over Time"])
        if distracting_timeline:
            for title, hourly_points in distracting_timeline:
                report_lines.append(title)
                for hour_label, total_seconds in hourly_points:
                    report_lines.append(f"- {hour_label} {format_duration(total_seconds)} {_bar(total_seconds, max(total_seconds for _, total_seconds in hourly_points), 10)}")
        else:
            report_lines.append("No distracting windows recorded.")

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

        report_lines.extend(["", "## Focus Blocks"])
        report_lines.append(f"- Focus blocks completed: {len(focus_blocks)}")
        report_lines.append(f"- Interruptions: {interruptions}")
        if recovery_seconds:
            average_recovery = sum(recovery_seconds) // len(recovery_seconds)
            report_lines.append(f"- Average recovery after distraction: {format_duration(average_recovery)}")
        else:
            report_lines.append("- Average recovery after distraction: n/a")

        if focus_blocks:
            for index, block in enumerate(
                sorted(focus_blocks, key=lambda item: (-item.duration_seconds, item.start))[:3],
                start=1,
            ):
                tag_text = f" [{', '.join(block.context_tags)}]" if block.context_tags else ""
                report_lines.append(
                    f"{index}. {block.start.strftime('%H:%M')} - {block.end.strftime('%H:%M')} "
                    f"on {block.primary_app} - {format_duration(block.duration_seconds)}{tag_text}"
                )
        else:
            report_lines.append("No focus blocks detected.")

        report_lines.extend(["", "## Comparisons", "", "### What Changed From Yesterday"])
        if previous_day is not None:
            report_lines.append(
                f"- Productive time: {_delta_label(totals['productive_seconds'], previous_day.totals['productive_seconds'])}"
            )
            report_lines.append(
                f"- Distracting time: {_delta_label(totals['distracting_seconds'], previous_day.totals['distracting_seconds'])}"
            )
            report_lines.append(
                f"- Focus blocks: {len(focus_blocks) - previous_day.focus_block_count:+d}"
            )
            report_lines.append(
                f"- App/window switches: {switch_count - previous_day.switch_count:+d}"
            )
        else:
            report_lines.append("No previous day data available.")

        report_lines.extend(["", "### Best Day This Week"])
        if best_week_day is not None:
            report_lines.append(
                f"- Best day: {best_week_day.date_value.isoformat()} "
                f"with {format_duration(best_week_day.totals['productive_seconds'])} productive time"
            )
            if best_week_day.date_value == anchor_date:
                report_lines.append("- Today is currently the strongest day this week.")
            else:
                gap = best_week_day.totals["productive_seconds"] - totals["productive_seconds"]
                report_lines.append(f"- Gap to best day: {format_duration(max(0, gap))}")
        else:
            report_lines.append("No weekly comparison data available.")

        report_lines.extend(
            [
                "",
                "## Recommendation",
                recommendation,
            ]
        )

        report_text = "\n".join(report_lines).strip() + "\n"
        report_path = self.reports_dir / report_filename
        report_path.write_text(report_text, encoding="utf-8")

        self.database.upsert_daily_summary(
            date=date_key,
            productive_seconds=totals["productive_seconds"],
            distracting_seconds=totals["distracting_seconds"],
            neutral_seconds=totals["neutral_seconds"],
            unknown_seconds=totals["unknown_seconds"],
            summary_text=recommendation,
        )

        return report_path

    def _top_apps(self, rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
        app_totals: Counter[str] = Counter()
        for row in rows:
            app_totals[str(row.get("app_name") or "unknown")] += int(row.get("duration_seconds") or 0)

        return [
            {"app_name": app_name, "total_seconds": total_seconds}
            for app_name, total_seconds in sorted(app_totals.items(), key=lambda item: (-item[1], item[0]))[:limit]
        ]

    def _top_distracting_titles(self, rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
        title_totals: Counter[str] = Counter()
        for row in rows:
            if str(row.get("category") or "").lower() != "distracting":
                continue

            title = str(row.get("window_title") or "").strip() or str(row.get("app_name") or "unknown")
            title_totals[title] += int(row.get("duration_seconds") or 0)

        return [
            {"title": title, "total_seconds": total_seconds}
            for title, total_seconds in sorted(title_totals.items(), key=lambda item: (-item[1], item[0]))[:limit]
        ]
