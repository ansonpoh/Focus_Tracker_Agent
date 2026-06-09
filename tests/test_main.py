import os

from main import (
    DEFAULT_SETTINGS,
    build_emailer,
    build_nudge_config,
    clear_pending_final_report,
    configure_logging,
    deliver_report,
    load_env_file,
    load_emailed_report_receipts,
    load_pending_final_report,
    load_settings_from_data,
    mark_report_emailed,
    mark_pending_final_report,
    retry_pending_final_report,
    run_retention_cleanup,
    should_email_report,
    validate_runtime_environment,
)


def test_load_settings_falls_back_on_invalid_values() -> None:
    settings = load_settings_from_data(
        {
            "tracking_interval_seconds": "bad",
            "nudge_cooldown_minutes": None,
            "daily_report_time": "99:99",
            "nudge_thresholds": {
                "distracting_minutes_threshold": "bad",
                "distracting_window_minutes": 25,
            },
        }
    )

    assert settings["tracking_interval_seconds"] == DEFAULT_SETTINGS["tracking_interval_seconds"]
    assert settings["nudge_cooldown_minutes"] == DEFAULT_SETTINGS["nudge_cooldown_minutes"]
    assert settings["daily_report_time"] == DEFAULT_SETTINGS["daily_report_time"]
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
        now=__import__("datetime").datetime(2026, 6, 9, 12, 0, 0),
    )

    assert deleted_rows == 1


def test_pending_final_report_marker_round_trip(tmp_path) -> None:
    pending_path = tmp_path / "pending_final_report.json"

    mark_pending_final_report("2026-06-09", pending_path)

    payload = load_pending_final_report(pending_path)

    assert payload is not None
    assert payload["report_date"] == "2026-06-09"
    assert payload["created_at"]

    clear_pending_final_report(pending_path)

    assert load_pending_final_report(pending_path) is None


def test_retry_pending_final_report_delivers_and_clears_marker(tmp_path) -> None:
    pending_path = tmp_path / "pending_final_report.json"
    logger = configure_logging(tmp_path / "pending-retry.log")
    mark_pending_final_report("2026-06-09", pending_path)

    class StubReporter:
        def __init__(self, reports_dir) -> None:
            self.reports_dir = reports_dir
            self.daily_calls = []
            self.weekly_calls = []
            self.monthly_calls = []

        def generate_daily_report(self, report_date: str):
            self.daily_calls.append(report_date)
            report_path = self.reports_dir / f"focus-report-{report_date}.md"
            report_path.write_text("# Focus Report\n", encoding="utf-8")
            return report_path

        def generate_weekly_report(self, report_date: str):
            self.weekly_calls.append(report_date)

        def generate_monthly_report(self, report_date: str):
            self.monthly_calls.append(report_date)

    class StubEmailer:
        def __init__(self) -> None:
            self.sent_paths = []

        def send_report(self, report_path) -> None:
            self.sent_paths.append(report_path)

    reporter = StubReporter(tmp_path)
    emailer = StubEmailer()

    report_path = retry_pending_final_report(reporter, emailer, logger, pending_path)

    assert report_path == tmp_path / "focus-report-2026-06-09.md"
    assert reporter.daily_calls == ["2026-06-09"]
    assert reporter.weekly_calls == ["2026-06-09"]
    assert reporter.monthly_calls == ["2026-06-09"]
    assert emailer.sent_paths == [report_path]
    assert pending_path.exists() is False


def test_retry_pending_final_report_keeps_marker_on_failure(tmp_path) -> None:
    pending_path = tmp_path / "pending_final_report.json"
    logger = configure_logging(tmp_path / "pending-retry-failure.log")
    mark_pending_final_report("2026-06-09", pending_path)

    class FailingReporter:
        def generate_daily_report(self, report_date: str):
            raise RuntimeError(f"cannot render {report_date}")

        def generate_weekly_report(self, report_date: str):
            assert False, "weekly report should not run after daily failure"

        def generate_monthly_report(self, report_date: str):
            assert False, "monthly report should not run after daily failure"

    class StubEmailer:
        def send_report(self, report_path) -> None:
            assert False, f"email should not send for {report_path}"

    report_path = retry_pending_final_report(FailingReporter(), StubEmailer(), logger, pending_path)

    assert report_path is None
    assert pending_path.exists() is True


def test_report_email_receipts_prevent_duplicate_delivery(tmp_path) -> None:
    receipts_path = tmp_path / "emailed_report_receipts.json"

    assert should_email_report("2026-06-09", receipts_path) is True

    mark_report_emailed("2026-06-09", receipts_path)

    assert should_email_report("2026-06-09", receipts_path) is False
    assert load_emailed_report_receipts(receipts_path) == {"2026-06-09"}

    mark_report_emailed("2026-06-09", receipts_path)

    assert load_emailed_report_receipts(receipts_path) == {"2026-06-09"}


def test_deliver_report_skips_second_email_for_same_date(tmp_path) -> None:
    logger = configure_logging(tmp_path / "duplicate-email.log")
    receipts_path = tmp_path / "emailed_report_receipts.json"

    class StubReporter:
        def __init__(self, reports_dir) -> None:
            self.reports_dir = reports_dir

        def generate_daily_report(self, report_date: str):
            report_path = self.reports_dir / f"focus-report-{report_date}.md"
            report_path.write_text("# Focus Report\n", encoding="utf-8")
            return report_path

        def generate_weekly_report(self, report_date: str):
            return None

        def generate_monthly_report(self, report_date: str):
            return None

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
    first_path = deliver_report(reporter, emailer, "2026-06-09", logger, receipts_path)
    second_path = deliver_report(reporter, emailer, "2026-06-09", logger, receipts_path)

    assert first_path == second_path
    assert emailer.sent_paths == [first_path]


def build_test_database(tmp_path):
    from database import FocusDatabase

    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()
    return database
