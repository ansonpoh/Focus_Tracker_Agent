from database import FocusDatabase
from reporter import DailyReporter, longest_productive_session


def test_longest_productive_session_breaks_on_large_gap() -> None:
    rows = [
        {"timestamp": "2026-06-09T09:00:00", "app_name": "Code.exe", "category": "productive", "duration_seconds": 5},
        {"timestamp": "2026-06-09T09:00:05", "app_name": "Code.exe", "category": "productive", "duration_seconds": 5},
        {"timestamp": "2026-06-09T09:00:30", "app_name": "Chrome.exe", "category": "productive", "duration_seconds": 5},
    ]

    duration, label = longest_productive_session(rows)

    assert duration == 10
    assert label == "Code.exe"


def test_weekly_report_aggregates_multiple_days(tmp_path) -> None:
    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()
    reporter = DailyReporter(database=database, reports_dir=tmp_path / "reports")

    rows = [
        ("2026-06-08T09:00:00", "Code.exe", "Editor", "productive", 600),
        ("2026-06-09T10:00:00", "chrome.exe", "YouTube", "distracting", 300),
        ("2026-06-10T11:00:00", "explorer.exe", "Files", "neutral", 120),
    ]
    for timestamp, app_name, window_title, category, duration_seconds in rows:
        database.insert_activity(
            timestamp=timestamp,
            app_name=app_name,
            window_title=window_title,
            category=category,
            duration_seconds=duration_seconds,
        )

    report_path = reporter.generate_weekly_report("2026-06-10")
    report_text = report_path.read_text(encoding="utf-8")

    assert report_path.name == "focus-report-week-2026-06-08.md"
    assert "# Focus Report - Week of 2026-06-08" in report_text
    assert "Total tracked time: 17m" in report_text
    assert "Productive: 10m" in report_text
    assert "Distracting: 5m" in report_text
    assert "Neutral: 2m" in report_text


def test_monthly_report_uses_month_boundaries(tmp_path) -> None:
    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()
    reporter = DailyReporter(database=database, reports_dir=tmp_path / "reports")

    in_month_rows = [
        ("2026-06-01T09:00:00", "Code.exe", "Editor", "productive", 600),
        ("2026-06-30T17:00:00", "chrome.exe", "YouTube", "distracting", 300),
    ]
    out_of_month_row = ("2026-07-01T09:00:00", "explorer.exe", "Files", "neutral", 1200)

    for timestamp, app_name, window_title, category, duration_seconds in in_month_rows + [out_of_month_row]:
        database.insert_activity(
            timestamp=timestamp,
            app_name=app_name,
            window_title=window_title,
            category=category,
            duration_seconds=duration_seconds,
        )

    report_path = reporter.generate_monthly_report("2026-06-15")
    report_text = report_path.read_text(encoding="utf-8")

    assert report_path.name == "focus-report-month-2026-06.md"
    assert "# Focus Report - 2026-06" in report_text
    assert "Total tracked time: 15m" in report_text
    assert "Neutral: 0s" in report_text


def test_daily_report_includes_sites_tags_and_focus_block_metrics(tmp_path) -> None:
    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()
    reporter = DailyReporter(database=database, reports_dir=tmp_path / "reports")

    comparison_rows = [
        ("2026-06-08T09:00:00", "Code.exe", "Deep work", "productive", 1800, ["work", "coding"], ""),
        ("2026-06-08T10:00:00", "chrome.exe", "YouTube - Google Chrome", "distracting", 300, ["video"], "youtube.com"),
        ("2026-06-10T08:00:00", "Code.exe", "Deep work", "productive", 2400, ["work", "coding"], ""),
        ("2026-06-10T09:00:00", "Code.exe", "Review", "productive", 1800, ["work", "coding"], ""),
    ]
    rows = [
        ("2026-06-09T09:00:00", "Code.exe", "Project editor", "productive", 300, ["work", "coding"], ""),
        ("2026-06-09T09:05:00", "Code.exe", "Project editor", "productive", 300, ["work", "coding"], ""),
        ("2026-06-09T09:10:00", "chrome.exe", "YouTube - Google Chrome", "distracting", 120, ["video"], "youtube.com"),
        ("2026-06-09T09:12:00", "chrome.exe", "Pull request - GitHub - Google Chrome", "productive", 600, ["work", "coding"], "github.com"),
        ("2026-06-09T09:22:00", "notion.exe", "Sprint notes", "productive", 300, ["work", "planning"], ""),
    ]
    for timestamp, app_name, window_title, category, duration_seconds, context_tags, site_hint in comparison_rows + rows:
        database.insert_activity(
            timestamp=timestamp,
            app_name=app_name,
            window_title=window_title,
            category=category,
            duration_seconds=duration_seconds,
            context_tags=context_tags,
            site_hint=site_hint,
        )

    report_path = reporter.generate_daily_report("2026-06-09")
    report_text = report_path.read_text(encoding="utf-8")

    assert "## Browser Sites" in report_text
    assert "1. github.com - 10m" in report_text
    assert "2. youtube.com - 2m" in report_text
    assert "## Context Tags" in report_text
    assert "1. work - 25m" in report_text
    assert "2. coding - 20m" in report_text
    assert "## Charts" in report_text
    assert "### Hourly Breakdown" in report_text
    assert "- 09:00 27m" in report_text
    assert "### Category Distribution" in report_text
    assert "- productive: 25m" in report_text
    assert "### Top Distracting Windows Over Time" in report_text
    assert "YouTube - Google Chrome" in report_text
    assert "## Focus Blocks" in report_text
    assert "- Focus blocks completed: 2" in report_text
    assert "- Interruptions: 1" in report_text
    assert "- Average recovery after distraction: 2m" in report_text
    assert "1. 09:12 - 09:27 on chrome.exe - 15m [work, coding, planning]" in report_text
    assert "## Comparisons" in report_text
    assert "### What Changed From Yesterday" in report_text
    assert "- Productive time: down 5m" in report_text
    assert "- Distracting time: down 3m" in report_text
    assert "### Best Day This Week" in report_text
    assert "- Best day: 2026-06-10 with 1h 10m productive time" in report_text
    assert "- Gap to best day: 45m" in report_text
    assert "Most distraction time came from github.com." not in report_text
    assert "Your best day this week was 2026-06-10, ahead by 45m of productive time." in report_text
