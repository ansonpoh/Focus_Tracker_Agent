from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
import json
import re

from database import FocusDatabase, count_switches


DEFAULT_MEANINGFUL_FOCUS_SECONDS = 5 * 60
SHORT_TRACKING_THRESHOLD_SECONDS = 15 * 60
LOW_CONTEXT_COVERAGE_RATIO = 0.2
RECENT_FOCUS_BLOCK_LIMIT = 3
BROWSER_APPS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"}
TITLE_SITE_ALIASES = {
    "youtube": ("YouTube", "youtube.com"),
    "github": ("GitHub", "github.com"),
    "reddit": ("Reddit", "reddit.com"),
    "tiktok": ("TikTok", "tiktok.com"),
    "instagram": ("Instagram", "instagram.com"),
    "netflix": ("Netflix", "netflix.com"),
    "stackoverflow": ("Stack Overflow", "stackoverflow.com"),
    "stack overflow": ("Stack Overflow", "stackoverflow.com"),
    "leetcode": ("LeetCode", "leetcode.com"),
    "figma": ("Figma", "figma.com"),
    "gmail": ("Gmail", "mail.google.com"),
    "google calendar": ("Google Calendar", "calendar.google.com"),
    "google docs": ("Google Docs", "docs.google.com"),
    "developer.mozilla.org": ("MDN", "developer.mozilla.org"),
    "linkedin": ("LinkedIn", "linkedin.com"),
    "x.com": ("X", "x.com"),
    "twitter": ("Twitter", "twitter.com"),
}
TITLE_DISPLAY_TO_DOMAIN = {
    display_name.lower(): domain for display_name, domain in TITLE_SITE_ALIASES.values()
}


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


def percentage_of_total(value: int, total: int) -> int:
    if total <= 0:
        return 0
    return int(round((max(0, value) / total) * 100))


def format_duration_share(value: int, total: int) -> str:
    return f"{format_duration(value)} — {percentage_of_total(value, total)}%"


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


def _normalize_domain(value: str) -> str:
    return value.strip().lower().removeprefix("www.")


def _pretty_site_label(value: str) -> str:
    normalized = _normalize_domain(value)
    for display_name, domain in TITLE_SITE_ALIASES.values():
        if normalized == domain:
            return display_name
    return value


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


@dataclass
class BrowserSiteSummary:
    label: str
    total_seconds: int
    detected_from_title: bool


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


def _browser_site_detection(row: dict[str, Any]) -> tuple[str, bool] | None:
    app_name = str(row.get("app_name") or "").strip().lower()
    if app_name not in BROWSER_APPS:
        return None

    site_hint = _normalize_domain(str(row.get("site_hint") or ""))
    if site_hint:
        return site_hint, False

    title = str(row.get("window_title") or "").strip().lower()
    direct_matches = re.findall(r"(localhost(?::\d+)?|(?:[a-z0-9-]+\.)+[a-z]{2,})", title)
    if direct_matches:
        first_match = _normalize_domain(direct_matches[0])
        return ("localhost" if first_match.startswith("localhost") else first_match), True

    for alias, (display_name, domain) in TITLE_SITE_ALIASES.items():
        if alias in title:
            if domain:
                return display_name, True

    return None


def extract_browser_sites(rows: list[dict[str, Any]], limit: int = 5) -> list[BrowserSiteSummary]:
    totals: dict[str, BrowserSiteSummary] = {}

    for row in rows:
        detection = _browser_site_detection(row)
        if detection is None:
            continue

        label, detected_from_title = detection
        duration = int(row.get("duration_seconds") or 0)
        key = TITLE_DISPLAY_TO_DOMAIN.get(label.lower(), _normalize_domain(label))
        if key not in totals:
            totals[key] = BrowserSiteSummary(label=label, total_seconds=0, detected_from_title=detected_from_title)
        totals[key].total_seconds += duration
        totals[key].detected_from_title = totals[key].detected_from_title and detected_from_title
        if not detected_from_title:
            totals[key].label = _normalize_domain(label)

    return sorted(totals.values(), key=lambda item: (-item.total_seconds, item.label.lower()))[:limit]


def top_distracting_sites(rows: list[dict[str, Any]], limit: int = 5) -> list[tuple[str, int]]:
    totals: Counter[str] = Counter()
    for row in rows:
        if str(row.get("category") or "").lower() != "distracting":
            continue
        detection = _browser_site_detection(row)
        if detection is None:
            continue
        label, detected_from_title = detection
        site_label = label if detected_from_title else _normalize_domain(label)
        totals[site_label] += int(row.get("duration_seconds") or 0)

    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:limit]


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
        ("Productive", totals["productive_seconds"]),
        ("Distracting", totals["distracting_seconds"]),
        ("Neutral", totals["neutral_seconds"]),
        ("Unknown", totals["unknown_seconds"]),
    ]


def best_day_this_week(database: FocusDatabase, target_date: date) -> DaySnapshot | None:
    snapshots = comparable_week_snapshots(database, target_date)
    if len(snapshots) < 2:
        return None

    return max(
        snapshots,
        key=lambda item: (
            item.totals["productive_seconds"],
            item.longest_focus_seconds,
            -item.switch_count,
            item.date_value.isoformat(),
        ),
    )


def comparable_week_snapshots(database: FocusDatabase, target_date: date) -> list[DaySnapshot]:
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
    return snapshots


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


def _context_coverage_ratio(rows: list[dict[str, Any]], total_seconds: int) -> float:
    if total_seconds <= 0:
        return 0.0

    covered_seconds = 0
    for row in rows:
        if _parse_context_tags(row):
            covered_seconds += int(row.get("duration_seconds") or 0)
    return covered_seconds / total_seconds


def calculate_focus_score(
    *,
    totals: dict[str, int],
    switch_count: int,
    longest_session_seconds: int,
    recovery_seconds: list[int],
) -> int:
    total_seconds = totals["total_seconds"]
    if total_seconds <= 0:
        return 0

    productive_ratio = totals["productive_seconds"] / total_seconds
    distracting_ratio = totals["distracting_seconds"] / total_seconds
    tracked_hours = max(total_seconds / 3600, 0.25)
    switch_baseline = tracked_hours * 8
    switch_component = max(0.0, 1.0 - min(switch_count / switch_baseline, 1.0)) * 10
    focus_component = min(longest_session_seconds / (30 * 60), 1.0) * 20

    if totals["distracting_seconds"] <= 0:
        recovery_component = 5.0
    elif recovery_seconds:
        average_recovery = sum(recovery_seconds) / len(recovery_seconds)
        recovery_component = max(0.0, 1.0 - min(average_recovery / (15 * 60), 1.0)) * 5
    else:
        recovery_component = 0.0

    score = (
        productive_ratio * 45
        + (1.0 - distracting_ratio) * 20
        + switch_component
        + focus_component
        + recovery_component
    )
    return max(0, min(100, int(round(score))))


def data_quality_warnings(
    *,
    rows: list[dict[str, Any]],
    totals: dict[str, int],
    previous_day: DaySnapshot | None,
) -> list[str]:
    warnings: list[str] = []

    if totals["total_seconds"] < SHORT_TRACKING_THRESHOLD_SECONDS:
        warnings.append("Total tracked time is short, so conclusions are preliminary.")

    if previous_day is None:
        warnings.append("No previous day data is available.")

    browser_rows = [row for row in rows if str(row.get("app_name") or "").strip().lower() in BROWSER_APPS]
    if browser_rows and not any(str(row.get("site_hint") or "").strip() for row in browser_rows):
        warnings.append("Browser domain detection appears unavailable.")

    if totals["total_seconds"] > 0 and _context_coverage_ratio(rows, totals["total_seconds"]) < LOW_CONTEXT_COVERAGE_RATIO:
        warnings.append("Context tags cover only a small part of tracked time.")

    if totals["unknown_seconds"] > 0:
        warnings.append("Unknown time exists.")

    return warnings


def meaningful_focus_block_count(
    focus_blocks: list[FocusBlock],
    *,
    threshold_seconds: int = DEFAULT_MEANINGFUL_FOCUS_SECONDS,
) -> int:
    return sum(1 for block in focus_blocks if block.duration_seconds >= threshold_seconds)


def _top_apps(rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    app_totals: dict[str, dict[str, Any]] = {}
    for row in rows:
        app_name = str(row.get("app_name") or "unknown")
        category = str(row.get("category") or "unknown").lower()
        duration = int(row.get("duration_seconds") or 0)
        if app_name not in app_totals:
            app_totals[app_name] = {"app_name": app_name, "total_seconds": 0, "categories": Counter()}
        app_totals[app_name]["total_seconds"] += duration
        app_totals[app_name]["categories"][category] += duration

    results: list[dict[str, Any]] = []
    for app_name, payload in app_totals.items():
        dominant_category = sorted(
            payload["categories"].items(),
            key=lambda item: (-item[1], item[0]),
        )[0][0].capitalize()
        results.append(
            {
                "app_name": app_name,
                "total_seconds": payload["total_seconds"],
                "classification": dominant_category,
            }
        )

    return sorted(results, key=lambda item: (-item["total_seconds"], item["app_name"].lower()))[:limit]


def _top_distracting_titles(rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
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


def _timeline_notes(
    rows: list[dict[str, Any]],
    top_titles: list[dict[str, Any]],
) -> dict[int, str]:
    notes: dict[int, str] = {}
    top_title = top_titles[0]["title"] if top_titles else ""

    for hour, productive, distracting, _, _ in hourly_breakdown(rows):
        hour_rows = [
            row
            for row in rows
            if isinstance(row.get("timestamp"), str) and datetime.fromisoformat(str(row["timestamp"])).hour == hour
        ]
        if top_title and any(str(row.get("window_title") or "").strip() == top_title for row in hour_rows):
            notes[hour] = "Main distraction occurred"
            continue
        if productive > 0 and productive < DEFAULT_MEANINGFUL_FOCUS_SECONDS:
            notes[hour] = "Brief productive burst"
            continue
        if productive >= DEFAULT_MEANINGFUL_FOCUS_SECONDS:
            notes[hour] = "Sustained productive work"

    return notes


def main_finding_for_report(
    *,
    totals: dict[str, int],
    top_titles: list[dict[str, Any]],
    top_apps: list[dict[str, Any]],
    browser_sites: list[BrowserSiteSummary],
    longest_session_seconds: int,
    meaningful_blocks: int,
) -> str:
    if totals["total_seconds"] <= 0:
        return "No tracked activity was captured, so there is no session pattern to interpret yet."

    if top_titles and totals["distracting_seconds"] >= totals["productive_seconds"]:
        top_title = top_titles[0]
        distracting_site = next((site for site in browser_sites if site.total_seconds >= top_title["total_seconds"]), None)
        distraction_label = (
            _pretty_site_label(distracting_site.label)
            if distracting_site is not None
            else top_title["title"]
        )
        finding = f"Most tracked time was spent on a single {distraction_label} distraction"
        if longest_session_seconds < DEFAULT_MEANINGFUL_FOCUS_SECONDS or meaningful_blocks == 0:
            primary_work_app = next((row["app_name"] for row in top_apps if row["classification"] == "Productive"), "productive apps")
            return (
                f"{finding}. Productive work happened briefly in {primary_work_app}, "
                "but focus sessions were too short to count as sustained work."
            )
        return f"{finding}. Productive work was present, but it did not outweigh the distraction-heavy part of the session."

    if totals["productive_seconds"] > totals["distracting_seconds"] and meaningful_blocks > 0:
        return "Productive work led the session, and at least one focus block was long enough to count as sustained work."

    if longest_session_seconds < DEFAULT_MEANINGFUL_FOCUS_SECONDS:
        return "Tracked work was fragmented into short attempts, so momentum never built into a meaningful focus block."

    return "The session mixed productive and non-productive time closely enough that no single pattern dominated."


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
        return "Start the tracker earlier and capture one 15-minute work block to create a useful baseline."

    top_focus_app = focus_blocks[0].primary_app if focus_blocks else "Code.exe"

    if totals["distracting_seconds"] >= totals["productive_seconds"]:
        if top_distracting_site_rows:
            top_site, _ = top_distracting_site_rows[0]
            site_name = _pretty_site_label(top_site)
            return (
                f"Start with one 15-minute protected work block before opening browser tabs tied to {site_name}. "
                f"The next goal is one uninterrupted 10-minute {top_focus_app} session."
            )
        return (
            "Start with one 15-minute protected work block before opening your browser. "
            f"The next goal is one uninterrupted 10-minute {top_focus_app} session."
        )

    if interruptions >= 3 and recovery_seconds:
        return (
            "Protect one 20-minute block with notifications off. "
            f"The next goal is to keep recovery after distractions under {format_duration(5 * 60)}."
        )

    if previous_day is not None and totals["productive_seconds"] < previous_day.totals["productive_seconds"]:
        return (
            "Repeat yesterday's opening work pattern earlier in the day. "
            f"The next goal is one uninterrupted {format_duration(max(10 * 60, longest_session_seconds + 5 * 60))} focus block."
        )

    if best_week_day is not None and previous_day is not None:
        return (
            "Reuse the setup from your strongest workday this week. "
            f"The next goal is to match at least {format_duration(max(10 * 60, longest_session_seconds))} in a single sitting."
        )

    if switch_count >= 20:
        return (
            "Choose one task and keep only the needed app visible for a 15-minute block. "
            f"The next goal is fewer than {max(5, switch_count // 2)} app or window switches in that block."
        )

    if hourly_rows:
        best_hour = max(hourly_rows, key=lambda item: (item[1], -item[0]))
        if best_hour[1] > 0:
            return (
                f"Protect the hour starting at {best_hour[0]:02d}:00 for focused work. "
                f"The next goal is one uninterrupted {format_duration(max(10 * 60, longest_session_seconds))} session in that window."
            )

    return (
        "Begin with one 15-minute protected block on your most important task. "
        f"The next goal is one uninterrupted 10-minute {top_focus_app} session."
    )


def _markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


class DailyReporter:
    def __init__(
        self,
        database: FocusDatabase,
        reports_dir: Path,
        *,
        meaningful_focus_block_seconds: int = DEFAULT_MEANINGFUL_FOCUS_SECONDS,
    ) -> None:
        self.database = database
        self.reports_dir = reports_dir
        self.meaningful_focus_block_seconds = meaningful_focus_block_seconds
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_daily_report(self, report_date: str | None = None) -> Path:
        date_value = report_date or datetime.now().date().isoformat()
        start = datetime.fromisoformat(f"{date_value}T00:00:00")
        end = start + timedelta(days=1)
        return self._generate_period_report(
            label=f"Focus Report — {date_value}",
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
            label=f"Focus Report — Week of {week_start.isoformat()}",
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
            label=f"Focus Report — {month_start.strftime('%Y-%m')}",
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
        top_apps = _top_apps(rows)
        top_titles = _top_distracting_titles(rows)
        browser_sites = extract_browser_sites(rows)
        top_distracting_site_rows = top_distracting_sites(rows)
        tag_rows = top_context_tags(rows)
        hourly_rows = hourly_breakdown(rows)
        switch_count = count_switches(rows)
        longest_session_seconds, longest_session_app = longest_productive_session(rows)
        focus_blocks = build_focus_blocks(rows)
        interruptions, recovery_seconds = interruption_metrics(rows)
        previous_day = previous_day_snapshot(self.database, anchor_date)
        week_snapshots = comparable_week_snapshots(self.database, anchor_date)
        best_week_day = best_day_this_week(self.database, anchor_date)
        focus_score = calculate_focus_score(
            totals=totals,
            switch_count=switch_count,
            longest_session_seconds=longest_session_seconds,
            recovery_seconds=recovery_seconds,
        )
        meaningful_blocks = meaningful_focus_block_count(
            focus_blocks,
            threshold_seconds=self.meaningful_focus_block_seconds,
        )
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
        main_finding = main_finding_for_report(
            totals=totals,
            top_titles=top_titles,
            top_apps=top_apps,
            browser_sites=browser_sites,
            longest_session_seconds=longest_session_seconds,
            meaningful_blocks=meaningful_blocks,
        )
        quality_warnings = data_quality_warnings(
            rows=rows,
            totals=totals,
            previous_day=previous_day,
        )
        timeline_notes = _timeline_notes(rows, top_titles)
        report_lines = [
            f"# {label}",
            "",
            "## Snapshot",
            f"Total tracked time: {format_duration(totals['total_seconds'])}",
            f"Focus score: {focus_score}/100",
            f"Productive: {format_duration_share(totals['productive_seconds'], totals['total_seconds'])}",
            f"Distracting: {format_duration_share(totals['distracting_seconds'], totals['total_seconds'])}",
            f"Neutral: {format_duration_share(totals['neutral_seconds'], totals['total_seconds'])}",
            f"Unknown: {format_duration_share(totals['unknown_seconds'], totals['total_seconds'])}",
            f"App/window switches: {switch_count}",
            f"Longest focus session: {format_duration(longest_session_seconds)}" if longest_session_seconds > 0 else "Longest focus session: 0s",
            "",
            "## Main Finding",
            main_finding,
            "",
            "## Time Breakdown",
            *_markdown_table(
                ["Category", "Time", "Share"],
                [
                    [name, format_duration(value), f"{percentage_of_total(value, totals['total_seconds'])}%"]
                    for name, value in category_distribution(totals)
                ],
            ),
            "",
            "## Top Apps",
        ]

        if top_apps:
            report_lines.extend(
                _markdown_table(
                    ["App", "Time", "Classification"],
                    [
                        [row["app_name"], format_duration(int(row["total_seconds"] or 0)), row["classification"]]
                        for row in top_apps
                    ],
                )
            )
        else:
            report_lines.append("No activity recorded.")

        report_lines.extend(["", "## Top Distraction"])
        if top_titles:
            top_title = top_titles[0]
            report_lines.extend(
                _markdown_table(
                    ["Window", "Time"],
                    [[top_title["title"], format_duration(int(top_title["total_seconds"] or 0))]],
                )
            )
        else:
            report_lines.append("No distracting sessions recorded.")

        report_lines.extend(["", "## Browser Sites"])
        if browser_sites:
            for site in browser_sites:
                if site.detected_from_title:
                    report_lines.append(
                        f"- {_pretty_site_label(site.label)} — {format_duration(site.total_seconds)}, detected from window title because browser URL/domain data was unavailable."
                    )
                else:
                    report_lines.append(f"- {site.label} — {format_duration(site.total_seconds)}")
        else:
            report_lines.append("No browser activity with a recognizable site was detected.")

        report_lines.extend(
            [
                "",
                "## Focus Sessions",
                f"Focus attempts: {len(focus_blocks)}",
                f"Meaningful focus blocks: {meaningful_blocks}",
                f"Longest sustained focus: {format_duration(longest_session_seconds)}" if longest_session_seconds > 0 else "Longest sustained focus: 0s",
                f"Interruptions: {interruptions}",
            ]
        )
        if recovery_seconds:
            average_recovery = sum(recovery_seconds) // len(recovery_seconds)
            report_lines.append(f"Average recovery after distraction: {format_duration(average_recovery)}")
        else:
            report_lines.append("Average recovery after distraction: n/a")

        if focus_blocks:
            report_lines.extend(["", "Recent Focus Blocks:"])
            for block in sorted(focus_blocks, key=lambda item: item.start)[-RECENT_FOCUS_BLOCK_LIMIT:]:
                tag_text = f" — {', '.join(block.context_tags)}" if block.context_tags else ""
                report_lines.append(
                    f"{block.start.strftime('%H:%M')}–{block.end.strftime('%H:%M')} — "
                    f"{block.primary_app} — {format_duration(block.duration_seconds)}{tag_text}"
                )
        else:
            report_lines.append("No focus attempts detected.")

        report_lines.extend(["", "## Timeline"])
        if hourly_rows:
            report_lines.extend(
                _markdown_table(
                    ["Hour", "Productive", "Distracting", "Notes"],
                    [
                        [
                            f"{hour:02d}:00",
                            format_duration(productive),
                            format_duration(distracting),
                            timeline_notes.get(hour, ""),
                        ]
                        for hour, productive, distracting, _, _ in hourly_rows
                    ],
                )
            )
        else:
            report_lines.append("No hourly data recorded.")

        report_lines.extend(["", "## Context Tags"])
        if tag_rows:
            report_lines.extend(
                _markdown_table(
                    ["Tag", "Time", "Share"],
                    [
                        [tag, format_duration(total_seconds), f"{percentage_of_total(total_seconds, totals['total_seconds'])}%"]
                        for tag, total_seconds in tag_rows
                    ],
                )
            )
        else:
            report_lines.append("No context tags recorded.")

        report_lines.extend(["", "## Comparisons"])
        if previous_day is not None:
            report_lines.append(f"Yesterday productive time: {_delta_label(totals['productive_seconds'], previous_day.totals['productive_seconds'])}")
            report_lines.append(f"Yesterday distracting time: {_delta_label(totals['distracting_seconds'], previous_day.totals['distracting_seconds'])}")
            report_lines.append(f"Yesterday focus attempts: {len(focus_blocks) - previous_day.focus_block_count:+d}")
            report_lines.append(f"Yesterday app/window switches: {switch_count - previous_day.switch_count:+d}")
        else:
            report_lines.append("No previous day data is available.")

        if len(week_snapshots) >= 2 and best_week_day is not None:
            if best_week_day.date_value == anchor_date:
                report_lines.append("Today is currently the strongest comparable day this week.")
            else:
                gap = max(0, best_week_day.totals["productive_seconds"] - totals["productive_seconds"])
                report_lines.append(
                    f"Best comparable day this week: {best_week_day.date_value.isoformat()} "
                    f"with {format_duration(best_week_day.totals['productive_seconds'])} productive time."
                )
                report_lines.append(f"Gap to that day: {format_duration(gap)}")
        else:
            report_lines.append("No meaningful weekly comparison yet. More than one day of data is needed.")

        report_lines.extend(["", "## Data Quality"])
        if quality_warnings:
            report_lines.extend(f"- {warning}" for warning in quality_warnings)
        else:
            report_lines.append("No major data quality warnings detected.")

        report_lines.extend(["", "## Recommendation", recommendation])

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
