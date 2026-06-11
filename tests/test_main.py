import os
from datetime import datetime

from main import (
    DEFAULT_SETTINGS,
    REPORT_KIND_DAILY,
    REPORT_KIND_MONTHLY,
    REPORT_KIND_WEEKLY,
    build_emailer,
    build_nudge_config,
    configure_logging,
    deliver_due_reports,
    deliver_report,
    get_latest_due_daily_date,
    get_latest_due_month_anchor,
    get_latest_due_week_anchor,
    load_env_file,
    load_emailed_report_receipts,
    load_settings_from_data,
    mark_report_emailed,
    parse_scheduled_delivery_time,
    run_retention_cleanup,
    should_email_report,
    validate_runtime_environment,
)


def test_load_settings_falls_back_on_invalid_values() -> None:
    settings = load_settings_from_data(
        {
            "tracking_interval_seconds": "bad",
            "nudge_cooldown_minutes": None,
            "scheduled_delivery_time": "99:99",
            "nudge_thresholds": {
                "distracting_minutes_threshold": "bad",
                "distracting_window_minutes": 25,
            },
        }
    )

    assert settings["tracking_interval_seconds"] == DEFAULT_SETTINGS["tracking_interval_seconds"]
    assert settings["nudge_cooldown_minutes"] == DEFAULT_SETTINGS["nudge_cooldown_minutes"]
    assert settings["scheduled_delivery_time"] == DEFAULT_SETTINGS["scheduled_delivery_time"]
    assert (
        settings["nudge_thresholds"]["distracting_minutes_threshold"]
        == DEFAULT_SETTINGS["nudge_thresholds"]["distracting_minutes_threshold"]
    )
    assert settings["nudge_thresholds"]["distracting_window_minutes"] == 25


def test_build_nudge_config_uses_loaded_values() -> None:
    settings = load_settings_from_data(DEFAULT_SETTINGS)
    config = build_nudge_config(settings)

    assert config.distracting_minutes_threshold == DEFAULT_SETTINGS["nudge_thresholds"]["distracting_minutes_threshold"]
    assert config.cooldown_minutes == DEFAULT_SETTINGS["nudge_cooldown_minutes"]


def test_load_settings_reads_email_report_config() -> None:
    settings = load_settings_from_data(
        {
            "email_reports": {
                "enabled": True,
                "recipient": "user@gmail.com",
                "sender": "agent@gmail.com",
                "smtp_port": 465,
                "use_tls": False,
            }
        }
    )

    assert settings["email_reports"]["enabled"] is True
    assert settings["email_reports"]["recipient"] == "user@gmail.com"
    assert settings["email_reports"]["sender"] == "agent@gmail.com"
    assert settings["email_reports"]["smtp_port"] == 465
    assert settings["email_reports"]["use_tls"] is False
    assert settings["classifier"]["enabled"] is True


def test_build_emailer_uses_loaded_settings() -> None:
    settings = load_settings_from_data(
        {
            "email_reports": {
                "enabled": True,
                "recipient": "user@gmail.com",
                "sender": "agent@gmail.com",
            }
        }
    )

    emailer = build_emailer(settings)

    assert emailer.settings.enabled is True
    assert emailer.settings.recipient == "user@gmail.com"
    assert emailer.settings.sender == "agent@gmail.com"


def test_load_settings_reads_classifier_config() -> None:
    settings = load_settings_from_data(
        {
            "classifier": {
                "enabled": False,
                "model": "gpt-5.4-mini",
                "api_timeout_seconds": 5,
                "min_confidence_threshold": 0.9,
                "reuse_provisional": False,
            }
        }
    )

    assert settings["classifier"]["enabled"] is False
    assert settings["classifier"]["model"] == "gpt-5.4-mini"
    assert settings["classifier"]["api_timeout_seconds"] == 5
    assert settings["classifier"]["min_confidence_threshold"] == 0.9
    assert settings["classifier"]["reuse_provisional"] is False


def test_load_env_file_sets_missing_variables_only(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        '\n'.join(
            [
                '# comment',
                'FOCUS_TRACKER_EMAIL_USERNAME="from-env-file@gmail.com"',
                'FOCUS_TRACKER_EMAIL_PASSWORD=app-password',
            ]
        ),
        encoding="utf-8",
    )

    original_username = os.environ.get("FOCUS_TRACKER_EMAIL_USERNAME")
    original_password = os.environ.get("FOCUS_TRACKER_EMAIL_PASSWORD")
    os.environ["FOCUS_TRACKER_EMAIL_USERNAME"] = "already-set@gmail.com"
    os.environ.pop("FOCUS_TRACKER_EMAIL_PASSWORD", None)

    try:
        load_env_file(env_path)

        assert os.environ["FOCUS_TRACKER_EMAIL_USERNAME"] == "already-set@gmail.com"
        assert os.environ["FOCUS_TRACKER_EMAIL_PASSWORD"] == "app-password"
    finally:
        if original_username is None:
            os.environ.pop("FOCUS_TRACKER_EMAIL_USERNAME", None)
        else:
            os.environ["FOCUS_TRACKER_EMAIL_USERNAME"] = original_username

        if original_password is None:
            os.environ.pop("FOCUS_TRACKER_EMAIL_PASSWORD", None)
        else:
            os.environ["FOCUS_TRACKER_EMAIL_PASSWORD"] = original_password


def test_validate_runtime_environment_rejects_non_windows(monkeypatch) -> None:
    monkeypatch.setattr("main.sys.platform", "linux")

    try:
        validate_runtime_environment()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "requires Windows interactive desktop APIs" in str(exc)


def test_validate_runtime_environment_allows_windows(monkeypatch) -> None:
    monkeypatch.setattr("main.sys.platform", "win32")

    validate_runtime_environment()


def test_configure_logging_writes_to_expected_file(tmp_path) -> None:
    logger = configure_logging(tmp_path / "focus_tracker.log")
    logger.info("logging smoke test")

    log_text = (tmp_path / "focus_tracker.log").read_text(encoding="utf-8")

    assert "INFO logging smoke test" in log_text


def test_run_retention_cleanup_uses_database_cutoff(tmp_path) -> None:
    database = build_test_database(tmp_path)
    logger = configure_logging(tmp_path / "retention.log")

    database.insert_activity(
        timestamp="2026-05-01T09:00:00",
        app_name="Code.exe",
        window_title="Editor",
        category="productive",
        duration_seconds=60,
    )
    database.insert_activity(
        timestamp="2026-06-09T09:00:00",
        app_name="Code.exe",
        window_title="Editor",
        category="productive",
        duration_seconds=60,
    )

    deleted_rows = run_retention_cleanup(
        database,
        30,
        logger,
        now=datetime(2026, 6, 9, 12, 0, 0),
    )

    assert deleted_rows == 1


def test_report_email_receipts_prevent_duplicate_delivery_per_kind(tmp_path) -> None:
    receipts_path = tmp_path / "emailed_report_receipts.json"

    assert should_email_report(REPORT_KIND_DAILY, "2026-06-09", receipts_path) is True
    assert should_email_report(REPORT_KIND_WEEKLY, "2026-06-08_week", receipts_path) is True

    mark_report_emailed(REPORT_KIND_DAILY, "2026-06-09", receipts_path)
    mark_report_emailed(REPORT_KIND_WEEKLY, "2026-06-08_week", receipts_path)

    assert should_email_report(REPORT_KIND_DAILY, "2026-06-09", receipts_path) is False
    assert should_email_report(REPORT_KIND_WEEKLY, "2026-06-08_week", receipts_path) is False
    assert load_emailed_report_receipts(receipts_path) == {
        REPORT_KIND_DAILY: {"2026-06-09"},
        REPORT_KIND_WEEKLY: {"2026-06-08_week"},
        REPORT_KIND_MONTHLY: set(),
    }


def test_load_emailed_report_receipts_migrates_legacy_daily_list(tmp_path) -> None:
    receipts_path = tmp_path / "emailed_report_receipts.json"
    receipts_path.write_text('["2026-06-09"]', encoding="utf-8")

    assert load_emailed_report_receipts(receipts_path) == {
        REPORT_KIND_DAILY: {"2026-06-09"},
        REPORT_KIND_WEEKLY: set(),
        REPORT_KIND_MONTHLY: set(),
    }


def test_deliver_report_skips_second_email_for_same_period(tmp_path) -> None:
    logger = configure_logging(tmp_path / "duplicate-email.log")
    receipts_path = tmp_path / "emailed_report_receipts.json"

    class StubReporter:
        def __init__(self, reports_dir) -> None:
            self.reports_dir = reports_dir
            self.daily_calls = []

        def generate_daily_report(self, report_date: str):
            self.daily_calls.append(report_date)
            report_path = self.reports_dir / f"focus-report-{report_date}.md"
            report_path.write_text("# Focus Report\n", encoding="utf-8")
            return report_path

        def generate_weekly_report(self, report_date: str):
            raise AssertionError("weekly report should not be generated")

        def generate_monthly_report(self, report_date: str):
            raise AssertionError("monthly report should not be generated")

    class StubEmailer:
        def __init__(self) -> None:
            self.sent_paths = []
            self.settings = type("Settings", (), {"recipient": "user@example.com"})()

        def send_report(self, report_path) -> None:
            self.sent_paths.append(report_path)

        def is_configured(self) -> bool:
            return True

    reporter = StubReporter(tmp_path)
    emailer = StubEmailer()
    first_path = deliver_report(reporter, emailer, REPORT_KIND_DAILY, "2026-06-09", logger, receipts_path)
    second_path = deliver_report(reporter, emailer, REPORT_KIND_DAILY, "2026-06-09", logger, receipts_path)

    assert first_path == second_path
    assert reporter.daily_calls == ["2026-06-09", "2026-06-09"]
    assert emailer.sent_paths == [first_path]


def test_latest_due_daily_date_respects_morning_cutoff() -> None:
    delivery_time = parse_scheduled_delivery_time("08:00")

    assert get_latest_due_daily_date(datetime(2026, 6, 10, 9, 0, 0), delivery_time) == "2026-06-09"
    assert get_latest_due_daily_date(datetime(2026, 6, 10, 7, 0, 0), delivery_time) == "2026-06-08"


def test_latest_due_week_anchor_respects_monday_cutoff() -> None:
    delivery_time = parse_scheduled_delivery_time("08:00")

    assert get_latest_due_week_anchor(datetime(2026, 6, 15, 9, 0, 0), delivery_time) == "2026-06-08"
    assert get_latest_due_week_anchor(datetime(2026, 6, 15, 7, 0, 0), delivery_time) == "2026-06-01"
    assert get_latest_due_week_anchor(datetime(2026, 6, 16, 9, 0, 0), delivery_time) == "2026-06-08"


def test_latest_due_month_anchor_respects_first_day_cutoff() -> None:
    delivery_time = parse_scheduled_delivery_time("08:00")

    assert get_latest_due_month_anchor(datetime(2026, 7, 1, 9, 0, 0), delivery_time) == "2026-06-30"
    assert get_latest_due_month_anchor(datetime(2026, 7, 1, 7, 0, 0), delivery_time) == "2026-05-31"
    assert get_latest_due_month_anchor(datetime(2026, 7, 2, 9, 0, 0), delivery_time) == "2026-06-30"


def test_deliver_due_reports_sends_latest_due_periods_once(tmp_path) -> None:
    logger = configure_logging(tmp_path / "deliver-due.log")
    receipts_path = tmp_path / "emailed_report_receipts.json"
    delivery_time = parse_scheduled_delivery_time("08:00")

    class StubReporter:
        def __init__(self, reports_dir) -> None:
            self.reports_dir = reports_dir
            self.calls = []

        def generate_daily_report(self, report_date: str):
            self.calls.append((REPORT_KIND_DAILY, report_date))
            path = self.reports_dir / f"focus-report-{report_date}.md"
            path.write_text("# Focus Report - Daily\n", encoding="utf-8")
            return path

        def generate_weekly_report(self, report_date: str):
            self.calls.append((REPORT_KIND_WEEKLY, report_date))
            path = self.reports_dir / f"focus-report-week-{report_date}.md"
            path.write_text("# Focus Report - Weekly\n", encoding="utf-8")
            return path

        def generate_monthly_report(self, report_date: str):
            self.calls.append((REPORT_KIND_MONTHLY, report_date))
            path = self.reports_dir / f"focus-report-month-{report_date}.md"
            path.write_text("# Focus Report - Monthly\n", encoding="utf-8")
            return path

    class StubEmailer:
        def __init__(self) -> None:
            self.sent_paths = []
            self.settings = type("Settings", (), {"recipient": "user@example.com"})()

        def send_report(self, report_path) -> None:
            self.sent_paths.append(report_path)

        def is_configured(self) -> bool:
            return True

    reporter = StubReporter(tmp_path)
    emailer = StubEmailer()

    delivered = deliver_due_reports(
        reporter,
        emailer,
        logger,
        delivery_time,
        now=datetime(2026, 7, 1, 9, 0, 0),
        receipts_path=receipts_path,
    )

    assert [(kind, key) for kind, key, _ in delivered] == [
        (REPORT_KIND_DAILY, "2026-06-30"),
        (REPORT_KIND_WEEKLY, "2026-06-22_week"),
        (REPORT_KIND_MONTHLY, "2026-06_month"),
    ]
    assert reporter.calls == [
        (REPORT_KIND_DAILY, "2026-06-30"),
        (REPORT_KIND_WEEKLY, "2026-06-22"),
        (REPORT_KIND_MONTHLY, "2026-06-30"),
    ]
    assert len(emailer.sent_paths) == 3

    second_delivered = deliver_due_reports(
        reporter,
        emailer,
        logger,
        delivery_time,
        now=datetime(2026, 7, 1, 9, 5, 0),
        receipts_path=receipts_path,
    )

    assert second_delivered == []


def build_test_database(tmp_path):
    from database import FocusDatabase

    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()
    return database
