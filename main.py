from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import os
import time

from classifier import load_classifier
from database import FocusDatabase
from emailer import EmailSettings, ReportEmailer
from nudger import NudgeConfig, Nudger
from observer import get_active_window
from reporter import DailyReporter


BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
ENV_PATH = BASE_DIR / ".env"
RULES_PATH = CONFIG_DIR / "rules.json"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
DATABASE_PATH = DATA_DIR / "focus_tracker.db"


DEFAULT_SETTINGS = {
    "tracking_interval_seconds": 5,
    "nudge_cooldown_minutes": 30,
    "nudge_thresholds": {
        "distracting_minutes_threshold": 20,
        "distracting_window_minutes": 30,
        "switch_threshold": 20,
        "switch_window_minutes": 10,
        "productive_minutes_threshold": 45,
    },
    "daily_report_time": "22:00",
    "email_reports": {
        "enabled": False,
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "sender": "",
        "recipient": "",
        "username_env": "FOCUS_TRACKER_EMAIL_USERNAME",
        "password_env": "FOCUS_TRACKER_EMAIL_PASSWORD",
        "use_tls": True,
        "attach_report_file": True,
    },
}


def _safe_int(value: object, default: int, *, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def _is_valid_time_string(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError:
        return False
    return True


def _safe_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _safe_string(value: object, default: str) -> str:
    if isinstance(value, str):
        return value
    return default


def load_env_file(env_path: Path = ENV_PATH) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        env_key = key.strip()
        if not env_key or env_key in os.environ:
            continue

        env_value = value.strip()
        if len(env_value) >= 2 and env_value[0] == env_value[-1] and env_value[0] in {"'", '"'}:
            env_value = env_value[1:-1]
        os.environ[env_key] = env_value


def load_settings_from_data(raw: object) -> dict:
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    if not isinstance(raw, dict):
        return settings

    settings["tracking_interval_seconds"] = _safe_int(
        raw.get("tracking_interval_seconds"),
        settings["tracking_interval_seconds"],
    )
    settings["nudge_cooldown_minutes"] = _safe_int(
        raw.get("nudge_cooldown_minutes"),
        settings["nudge_cooldown_minutes"],
    )
    if _is_valid_time_string(raw.get("daily_report_time")):
        settings["daily_report_time"] = str(raw["daily_report_time"])

    raw_thresholds = raw.get("nudge_thresholds", {})
    if isinstance(raw_thresholds, dict):
        thresholds = settings["nudge_thresholds"]
        for key, default_value in list(thresholds.items()):
            thresholds[key] = _safe_int(raw_thresholds.get(key), int(default_value))

    raw_email = raw.get("email_reports", {})
    if isinstance(raw_email, dict):
        email_settings = settings["email_reports"]
        email_settings["enabled"] = _safe_bool(raw_email.get("enabled"), bool(email_settings["enabled"]))
        email_settings["smtp_server"] = _safe_string(raw_email.get("smtp_server"), str(email_settings["smtp_server"]))
        email_settings["smtp_port"] = _safe_int(raw_email.get("smtp_port"), int(email_settings["smtp_port"]))
        email_settings["sender"] = _safe_string(raw_email.get("sender"), str(email_settings["sender"]))
        email_settings["recipient"] = _safe_string(raw_email.get("recipient"), str(email_settings["recipient"]))
        email_settings["username_env"] = _safe_string(raw_email.get("username_env"), str(email_settings["username_env"]))
        email_settings["password_env"] = _safe_string(raw_email.get("password_env"), str(email_settings["password_env"]))
        email_settings["use_tls"] = _safe_bool(raw_email.get("use_tls"), bool(email_settings["use_tls"]))
        email_settings["attach_report_file"] = _safe_bool(
            raw_email.get("attach_report_file"),
            bool(email_settings["attach_report_file"]),
        )

    return settings


def load_settings() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if not SETTINGS_PATH.exists():
        SETTINGS_PATH.write_text(json.dumps(DEFAULT_SETTINGS, indent=2), encoding="utf-8")
        return load_settings_from_data(DEFAULT_SETTINGS)

    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return load_settings_from_data(DEFAULT_SETTINGS)

    return load_settings_from_data(raw)


def build_nudge_config(settings: dict) -> NudgeConfig:
    thresholds = settings["nudge_thresholds"]
    return NudgeConfig(
        distracting_minutes_threshold=int(thresholds["distracting_minutes_threshold"]),
        distracting_window_minutes=int(thresholds["distracting_window_minutes"]),
        switch_threshold=int(thresholds["switch_threshold"]),
        switch_window_minutes=int(thresholds["switch_window_minutes"]),
        productive_minutes_threshold=int(thresholds["productive_minutes_threshold"]),
        cooldown_minutes=int(settings["nudge_cooldown_minutes"]),
    )


def build_emailer(settings: dict) -> ReportEmailer:
    email_settings = settings["email_reports"]
    return ReportEmailer(
        EmailSettings(
            enabled=bool(email_settings["enabled"]),
            smtp_server=str(email_settings["smtp_server"]),
            smtp_port=int(email_settings["smtp_port"]),
            sender=str(email_settings["sender"]),
            recipient=str(email_settings["recipient"]),
            username_env=str(email_settings["username_env"]),
            password_env=str(email_settings["password_env"]),
            use_tls=bool(email_settings["use_tls"]),
            attach_report_file=bool(email_settings["attach_report_file"]),
        )
    )


def deliver_report(reporter: DailyReporter, emailer: ReportEmailer, report_date: str) -> Path:
    report_path = reporter.generate_daily_report(report_date)
    reporter.generate_weekly_report(report_date)
    reporter.generate_monthly_report(report_date)
    emailer.send_report(report_path)
    return report_path


def schedule_daily_report(reporter: DailyReporter, emailer: ReportEmailer, time_str: str) -> None:
    import schedule

    def _run_report() -> None:
        report_date = datetime.now().date().isoformat()
        try:
            report_path = deliver_report(reporter, emailer, report_date)
            print(f"Report written to: {report_path}")
            if emailer.is_configured():
                print(f"Report emailed to: {emailer.settings.recipient}")
        except Exception as exc:
            print(f"[warn] Scheduled report delivery failed: {exc}")

    schedule.every().day.at(time_str).do(_run_report)


def main() -> None:
    import schedule

    load_env_file()
    settings = load_settings()

    database = FocusDatabase(DATABASE_PATH)
    database.initialize()

    classifier = load_classifier(RULES_PATH)
    nudger = Nudger(database=database, config=build_nudge_config(settings))
    reporter = DailyReporter(database=database, reports_dir=REPORTS_DIR)
    emailer = build_emailer(settings)

    schedule_daily_report(reporter, emailer, settings["daily_report_time"])

    interval_seconds = int(settings["tracking_interval_seconds"])
    print("Focus Tracker Agent running. Press Ctrl+C to stop.")

    try:
        while True:
            loop_started = time.monotonic()
            try:
                snapshot = get_active_window()
                classification = classifier.classify(snapshot.get("app_name"), snapshot.get("window_title"))
                database.insert_activity(
                    timestamp=str(snapshot.get("timestamp") or datetime.now().replace(microsecond=0).isoformat()),
                    app_name=str(snapshot.get("app_name") or "unknown"),
                    window_title=str(snapshot.get("window_title") or ""),
                    category=classification.category,
                    duration_seconds=interval_seconds,
                    context_tags=classification.context_tags,
                    site_hint=classification.site_hint,
                )
                nudger.check_nudges()
            except Exception as exc:
                print(f"[warn] Tracking iteration failed: {exc}")
            finally:
                schedule.run_pending()
                elapsed = time.monotonic() - loop_started
                sleep_for = max(0.0, interval_seconds - elapsed)
                time.sleep(sleep_for)
    except KeyboardInterrupt:
        print("Stopping tracker and generating final report...")
    finally:
        try:
            report_path = deliver_report(reporter, emailer, datetime.now().date().isoformat())
            print(f"Report written to: {report_path}")
            if emailer.is_configured():
                print(f"Report emailed to: {emailer.settings.recipient}")
        except Exception as exc:
            print(f"[warn] Could not generate final report: {exc}")


if __name__ == "__main__":
    main()
