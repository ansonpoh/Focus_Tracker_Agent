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
    assert "# Focus Report — Week of 2026-06-08" in report_text
    assert "Total tracked time: 17m" in report_text
    assert "Productive: 10m — 59%" in report_text
    assert "Distracting: 5m — 29%" in report_text
    assert "Neutral: 2m — 12%" in report_text


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
    assert "# Focus Report — 2026-06" in report_text
    assert "Total tracked time: 15m" in report_text
    assert "Neutral: 0s — 0%" in report_text


def test_daily_report_outputs_percentages_and_tables(tmp_path) -> None:
    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()
    reporter = DailyReporter(database=database, reports_dir=tmp_path / "reports")

    rows = [
        ("2026-06-09T14:41:00", "Code.exe", "Editor", "productive", 120, ["work", "coding"], ""),
        ("2026-06-09T14:43:00", "chrome.exe", "YouTube - Google Chrome", "distracting", 600, [], ""),
        ("2026-06-09T14:53:00", "Codex.exe", "Codex", "unknown", 15, [], ""),
    ]
    for timestamp, app_name, window_title, category, duration_seconds, context_tags, site_hint in rows:
        database.insert_activity(
            timestamp=timestamp,
            app_name=app_name,
            window_title=window_title,
            category=category,
            duration_seconds=duration_seconds,
            context_tags=context_tags,
            site_hint=site_hint,
        )

    report_text = reporter.generate_daily_report("2026-06-09").read_text(encoding="utf-8")

    assert "Productive: 2m — 16%" in report_text
    assert "Distracting: 10m — 82%" in report_text
    assert "Unknown: 15s — 2%" in report_text
    assert "## Time Breakdown" in report_text
    assert "| Category | Time | Share |" in report_text
    assert "| Productive | 2m | 16% |" in report_text
    assert "| Distracting | 10m | 82% |" in report_text


def test_browser_site_detection_uses_window_title_for_youtube(tmp_path) -> None:
    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()
    reporter = DailyReporter(database=database, reports_dir=tmp_path / "reports")

    database.insert_activity(
        timestamp="2026-06-09T14:41:00",
        app_name="chrome.exe",
        window_title="Rebuilding the 2016 Lakers — YouTube — Google Chrome",
        category="distracting",
        duration_seconds=600,
    )

    report_text = reporter.generate_daily_report("2026-06-09").read_text(encoding="utf-8")

    assert "## Browser Sites" in report_text
    assert "YouTube — 10m, detected from window title because browser URL/domain data was unavailable." in report_text
    assert "No browser activity with a recognizable site was detected." not in report_text


def test_comparisons_avoid_misleading_best_day_claim_with_single_day_of_data(tmp_path) -> None:
    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()
    reporter = DailyReporter(database=database, reports_dir=tmp_path / "reports")

    database.insert_activity(
        timestamp="2026-06-09T09:00:00",
        app_name="Code.exe",
        window_title="Editor",
        category="productive",
        duration_seconds=600,
    )

    report_text = reporter.generate_daily_report("2026-06-09").read_text(encoding="utf-8")

    assert "No meaningful weekly comparison yet. More than one day of data is needed." in report_text
    assert "Today is currently the strongest comparable day this week." not in report_text


def test_focus_attempts_and_meaningful_focus_blocks_are_reported_separately(tmp_path) -> None:
    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()
    reporter = DailyReporter(
        database=database,
        reports_dir=tmp_path / "reports",
        meaningful_focus_block_seconds=5 * 60,
    )

    rows = [
        ("2026-06-09T09:00:00", "Code.exe", "Editor", "productive", 120, ["work", "coding"], ""),
        ("2026-06-09T09:03:00", "chrome.exe", "YouTube", "distracting", 60, [], ""),
        ("2026-06-09T09:05:00", "Code.exe", "Editor", "productive", 360, ["work", "coding"], ""),
    ]
    for timestamp, app_name, window_title, category, duration_seconds, context_tags, site_hint in rows:
        database.insert_activity(
            timestamp=timestamp,
            app_name=app_name,
            window_title=window_title,
            category=category,
            duration_seconds=duration_seconds,
            context_tags=context_tags,
            site_hint=site_hint,
        )

    report_text = reporter.generate_daily_report("2026-06-09").read_text(encoding="utf-8")

    assert "Focus attempts: 2" in report_text
    assert "Meaningful focus blocks: 1" in report_text
    assert "Recent Focus Blocks:" in report_text


def test_data_quality_warns_for_short_tracking_windows(tmp_path) -> None:
    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()
    reporter = DailyReporter(database=database, reports_dir=tmp_path / "reports")

    rows = [
        ("2026-06-09T14:41:00", "chrome.exe", "YouTube - Google Chrome", "distracting", 600, [], ""),
        ("2026-06-09T14:53:00", "Codex.exe", "Codex", "unknown", 15, [], ""),
        ("2026-06-09T14:54:00", "Code.exe", "Editor", "productive", 10, [], ""),
    ]
    for timestamp, app_name, window_title, category, duration_seconds, context_tags, site_hint in rows:
        database.insert_activity(
            timestamp=timestamp,
            app_name=app_name,
            window_title=window_title,
            category=category,
            duration_seconds=duration_seconds,
            context_tags=context_tags,
            site_hint=site_hint,
        )

    report_text = reporter.generate_daily_report("2026-06-09").read_text(encoding="utf-8")

    assert "## Data Quality" in report_text
    assert "Total tracked time is short, so conclusions are preliminary." in report_text
    assert "No previous day data is available." in report_text
    assert "Browser domain detection appears unavailable." in report_text
    assert "Context tags cover only a small part of tracked time." in report_text
    assert "Unknown time exists." in report_text


def test_recommendation_is_specific_and_measurable(tmp_path) -> None:
    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()
    reporter = DailyReporter(database=database, reports_dir=tmp_path / "reports")

    rows = [
        ("2026-06-09T14:41:00", "Code.exe", "Editor", "productive", 120, ["work", "coding"], ""),
        ("2026-06-09T14:43:00", "chrome.exe", "YouTube - Google Chrome", "distracting", 600, [], ""),
    ]
    for timestamp, app_name, window_title, category, duration_seconds, context_tags, site_hint in rows:
        database.insert_activity(
            timestamp=timestamp,
            app_name=app_name,
            window_title=window_title,
            category=category,
            duration_seconds=duration_seconds,
            context_tags=context_tags,
            site_hint=site_hint,
        )

    report_text = reporter.generate_daily_report("2026-06-09").read_text(encoding="utf-8")

    assert "## Recommendation" in report_text
    assert "Start with one 15-minute protected work block before opening browser tabs tied to YouTube." in report_text
    assert "The next goal is one uninterrupted 10-minute Code.exe session." in report_text
