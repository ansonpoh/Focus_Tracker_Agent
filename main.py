from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import time

from classifier import load_classifier
from database import FocusDatabase
from nudger import NudgeConfig, Nudger
from observer import get_active_window
from reporter import DailyReporter


BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
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


def schedule_daily_report(reporter: DailyReporter, time_str: str) -> None:
    import schedule

    def _run_report() -> None:
        report_date = datetime.now().date().isoformat()
        reporter.generate_daily_report(report_date)

    schedule.every().day.at(time_str).do(_run_report)


def main() -> None:
    import schedule

    settings = load_settings()

    database = FocusDatabase(DATABASE_PATH)
    database.initialize()

    classifier = load_classifier(RULES_PATH)
    nudger = Nudger(database=database, config=build_nudge_config(settings))
    reporter = DailyReporter(database=database, reports_dir=REPORTS_DIR)

    schedule_daily_report(reporter, settings["daily_report_time"])

    interval_seconds = int(settings["tracking_interval_seconds"])
    print("Focus Tracker Agent running. Press Ctrl+C to stop.")

    try:
        while True:
            loop_started = time.monotonic()
            try:
                snapshot = get_active_window()
                category = classifier.classify(snapshot.get("app_name"), snapshot.get("window_title"))
                database.insert_activity(
                    timestamp=str(snapshot.get("timestamp") or datetime.now().replace(microsecond=0).isoformat()),
                    app_name=str(snapshot.get("app_name") or "unknown"),
                    window_title=str(snapshot.get("window_title") or ""),
                    category=category,
                    duration_seconds=interval_seconds,
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
            report_path = reporter.generate_daily_report(datetime.now().date().isoformat())
            print(f"Report written to: {report_path}")
        except Exception as exc:
            print(f"[warn] Could not generate final report: {exc}")


if __name__ == "__main__":
    main()
