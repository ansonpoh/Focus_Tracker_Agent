from __future__ import annotations

from datetime import date, datetime, time as time_value, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
import json
import logging
import os
import sys
import time

from classifier import load_classifier
from database import FocusDatabase
from dynamic_classifier import DynamicClassificationEngine, classifier_settings_from_dict
from emailer import EmailSettings, ReportEmailer
from nudger import DesktopNotifier, NudgeConfig
from observer import get_active_window
from reporter import DailyReporter
from adaptive_coach import AdaptiveCoach, agent_settings_from_dict


BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
ENV_PATH = BASE_DIR / ".env"
RULES_PATH = CONFIG_DIR / "rules.json"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
DATABASE_PATH = DATA_DIR / "focus_tracker.db"
LOG_PATH = DATA_DIR / "focus_tracker.log"
EMAILED_REPORT_RECEIPTS_PATH = DATA_DIR / "emailed_report_receipts.json"

REPORT_KIND_DAILY = "daily"
REPORT_KIND_WEEKLY = "weekly"
REPORT_KIND_MONTHLY = "monthly"
REPORT_KINDS = (REPORT_KIND_DAILY, REPORT_KIND_WEEKLY, REPORT_KIND_MONTHLY)


DEFAULT_SETTINGS = {
    "tracking_interval_seconds": 5,
    "nudge_cooldown_minutes": 30,
    "raw_activity_retention_days": 90,
    "nudge_thresholds": {
        "distracting_minutes_threshold": 20,
        "distracting_window_minutes": 30,
        "switch_threshold": 20,
        "switch_window_minutes": 10,
        "productive_minutes_threshold": 45,
    },
    "scheduled_delivery_time": "08:00",
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
    "classifier": {
        "enabled": True,
        "mode": "hybrid",
        "model": "gpt-5-mini",
        "api_base_url": "https://api.openai.com/v1/responses",
        "api_key_env": "OPENAI_API_KEY",
        "api_timeout_seconds": 10,
        "request_max_retries": 1,
        "min_confidence_threshold": 0.75,
        "reuse_provisional": True,
        "max_output_tokens": 300,
    },
    "agent": {
        "enabled": True,
        "default_mode": "adaptive_coach",
        "policy": {
            "productive_window_minutes": 30,
            "break_threshold_minutes": 45,
            "drifting_window_minutes": 30,
            "distracting_ratio_threshold": 0.5,
            "fragmented_window_minutes": 10,
            "fragmented_switch_threshold": 20,
            "neutral_window_minutes": 20,
        },
        "goal_defaults": {
            "daily_productive_minutes": 120,
            "focus_block_count": 3,
            "focus_block_duration_minutes": 25,
            "distracting_limit_minutes": 20,
            "blocked_sites": ["youtube.com", "reddit.com", "x.com"],
        },
        "intervention_cooldowns": {
            "encourage_focus": 45,
            "warn_drift": 30,
            "suggest_break": 60,
            "goal_reminder": 90,
        },
        "outcome_window_minutes": 15,
    },
}


LOGGER_NAME = "focus_tracker"


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


def _safe_float(value: object, default: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def configure_logging(log_path: Path = LOG_PATH) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    if not any(isinstance(handler, logging.StreamHandler) and not isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    resolved_log_path = log_path.resolve()
    file_handler_exists = False
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename).resolve() == resolved_log_path:
            file_handler_exists = True
            break

    if not file_handler_exists:
        file_handler = RotatingFileHandler(
            resolved_log_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def validate_runtime_environment() -> None:
    if sys.platform != "win32":
        raise RuntimeError(
            "Focus Tracker requires Windows interactive desktop APIs. "
            "This runtime cannot capture the active foreground window safely."
        )


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
    settings["raw_activity_retention_days"] = _safe_int(
        raw.get("raw_activity_retention_days"),
        settings["raw_activity_retention_days"],
    )
    if _is_valid_time_string(raw.get("scheduled_delivery_time")):
        settings["scheduled_delivery_time"] = str(raw["scheduled_delivery_time"])

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

    raw_classifier = raw.get("classifier", {})
    if isinstance(raw_classifier, dict):
        classifier_settings = settings["classifier"]
        classifier_settings["enabled"] = _safe_bool(raw_classifier.get("enabled"), bool(classifier_settings["enabled"]))
        classifier_settings["mode"] = _safe_string(raw_classifier.get("mode"), str(classifier_settings["mode"]))
        classifier_settings["model"] = _safe_string(raw_classifier.get("model"), str(classifier_settings["model"]))
        classifier_settings["api_base_url"] = _safe_string(
            raw_classifier.get("api_base_url"),
            str(classifier_settings["api_base_url"]),
        )
        classifier_settings["api_key_env"] = _safe_string(
            raw_classifier.get("api_key_env"),
            str(classifier_settings["api_key_env"]),
        )
        classifier_settings["api_timeout_seconds"] = _safe_int(
            raw_classifier.get("api_timeout_seconds"),
            int(classifier_settings["api_timeout_seconds"]),
        )
        classifier_settings["request_max_retries"] = _safe_int(
            raw_classifier.get("request_max_retries"),
            int(classifier_settings["request_max_retries"]),
            minimum=0,
        )
        classifier_settings["min_confidence_threshold"] = _safe_float(
            raw_classifier.get("min_confidence_threshold"),
            float(classifier_settings["min_confidence_threshold"]),
        )
        classifier_settings["reuse_provisional"] = _safe_bool(
            raw_classifier.get("reuse_provisional"),
            bool(classifier_settings["reuse_provisional"]),
        )
        classifier_settings["max_output_tokens"] = _safe_int(
            raw_classifier.get("max_output_tokens"),
            int(classifier_settings["max_output_tokens"]),
            minimum=64,
        )

    raw_agent = raw.get("agent", {})
    if isinstance(raw_agent, dict):
        agent_settings = settings["agent"]
        agent_settings["enabled"] = _safe_bool(raw_agent.get("enabled"), bool(agent_settings["enabled"]))
        agent_settings["default_mode"] = _safe_string(raw_agent.get("default_mode"), str(agent_settings["default_mode"]))

        raw_policy = raw_agent.get("policy", {})
        if isinstance(raw_policy, dict):
            policy_settings = agent_settings["policy"]
            for key, default_value in list(policy_settings.items()):
                if isinstance(default_value, float):
                    policy_settings[key] = _safe_float(raw_policy.get(key), float(default_value), minimum=0.0, maximum=1.0)
                else:
                    policy_settings[key] = _safe_int(raw_policy.get(key), int(default_value))

        raw_goal_defaults = raw_agent.get("goal_defaults", {})
        if isinstance(raw_goal_defaults, dict):
            goal_defaults = agent_settings["goal_defaults"]
            for key, default_value in list(goal_defaults.items()):
                if isinstance(default_value, list):
                    candidate = raw_goal_defaults.get(key)
                    if isinstance(candidate, list):
                        goal_defaults[key] = [str(item).strip().lower() for item in candidate if str(item).strip()]
                else:
                    goal_defaults[key] = _safe_int(raw_goal_defaults.get(key), int(default_value))

        raw_cooldowns = raw_agent.get("intervention_cooldowns", {})
        if isinstance(raw_cooldowns, dict):
            cooldown_settings = agent_settings["intervention_cooldowns"]
            for key, default_value in list(cooldown_settings.items()):
                cooldown_settings[key] = _safe_int(raw_cooldowns.get(key), int(default_value))

        agent_settings["outcome_window_minutes"] = _safe_int(
            raw_agent.get("outcome_window_minutes"),
            int(agent_settings["outcome_window_minutes"]),
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


def build_classification_engine(settings: dict, database: FocusDatabase) -> DynamicClassificationEngine:
    classifier_settings = classifier_settings_from_dict(dict(settings["classifier"]))
    return DynamicClassificationEngine(
        database=database,
        heuristic_classifier=load_classifier(RULES_PATH),
        settings=classifier_settings,
    )


def build_agent_settings(settings: dict):
    return agent_settings_from_dict(dict(settings["agent"]))


def ensure_default_goals(database: FocusDatabase, settings: dict) -> None:
    existing_goals = database.list_goals()
    if existing_goals:
        return

    goal_defaults = settings["agent"]["goal_defaults"]
    database.upsert_goal(
        goal_type="daily_productive_minutes",
        name="Daily productive minutes",
        target_value=int(goal_defaults["daily_productive_minutes"]),
        window_minutes=0,
        schedule_start="08:00",
        schedule_end="18:00",
        days_of_week=[0, 1, 2, 3, 4],
        config={},
    )
    database.upsert_goal(
        goal_type="focus_block_count",
        name="Meaningful focus blocks",
        target_value=int(goal_defaults["focus_block_count"]),
        window_minutes=int(goal_defaults["focus_block_duration_minutes"]),
        schedule_start="08:00",
        schedule_end="18:00",
        days_of_week=[0, 1, 2, 3, 4],
        config={},
    )
    database.upsert_goal(
        goal_type="distracting_limit",
        name="Distracting time limit",
        target_value=int(goal_defaults["distracting_limit_minutes"]),
        window_minutes=60,
        schedule_start="08:00",
        schedule_end="18:00",
        days_of_week=[0, 1, 2, 3, 4],
        config={"blocked_sites": list(goal_defaults.get("blocked_sites", []))},
    )


def _empty_receipts() -> dict[str, set[str]]:
    return {kind: set() for kind in REPORT_KINDS}


def load_emailed_report_receipts(path: Path | None = None) -> dict[str, set[str]]:
    target_path = path or EMAILED_REPORT_RECEIPTS_PATH
    if not target_path.exists():
        return _empty_receipts()

    try:
        payload = json.loads(target_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _empty_receipts()

    receipts = _empty_receipts()
    if isinstance(payload, list):
        receipts[REPORT_KIND_DAILY] = {item for item in payload if isinstance(item, str) and item}
        return receipts

    if not isinstance(payload, dict):
        return receipts

    for kind in REPORT_KINDS:
        raw_items = payload.get(kind, [])
        if isinstance(raw_items, list):
            receipts[kind] = {item for item in raw_items if isinstance(item, str) and item}

    return receipts


def save_emailed_report_receipts(receipts: dict[str, set[str]], path: Path | None = None) -> None:
    target_path = path or EMAILED_REPORT_RECEIPTS_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = {
        kind: sorted(receipts.get(kind, set()))
        for kind in REPORT_KINDS
    }
    target_path.write_text(json.dumps(serialized, indent=2), encoding="utf-8")


def mark_report_emailed(report_kind: str, period_key: str, path: Path | None = None) -> None:
    target_path = path or EMAILED_REPORT_RECEIPTS_PATH
    receipts = load_emailed_report_receipts(target_path)
    if period_key in receipts.get(report_kind, set()):
        return

    receipts.setdefault(report_kind, set()).add(period_key)
    save_emailed_report_receipts(receipts, target_path)


def should_email_report(report_kind: str, period_key: str, path: Path | None = None) -> bool:
    return period_key not in load_emailed_report_receipts(path).get(report_kind, set())


def parse_scheduled_delivery_time(time_str: str) -> time_value:
    parsed = datetime.strptime(time_str, "%H:%M")
    return parsed.time()


def get_daily_period_key(report_date: str) -> str:
    return report_date


def get_weekly_period_key(any_date: str) -> str:
    target_date = date.fromisoformat(any_date)
    week_start = target_date - timedelta(days=target_date.weekday())
    return f"{week_start.isoformat()}_week"


def get_monthly_period_key(any_date: str) -> str:
    target_date = date.fromisoformat(any_date)
    return f"{target_date.strftime('%Y-%m')}_month"


def get_period_key(report_kind: str, anchor_date: str) -> str:
    if report_kind == REPORT_KIND_DAILY:
        return get_daily_period_key(anchor_date)
    if report_kind == REPORT_KIND_WEEKLY:
        return get_weekly_period_key(anchor_date)
    if report_kind == REPORT_KIND_MONTHLY:
        return get_monthly_period_key(anchor_date)
    raise ValueError(f"Unsupported report kind: {report_kind}")


def generate_report_for_period(reporter: DailyReporter, report_kind: str, anchor_date: str) -> Path:
    if report_kind == REPORT_KIND_DAILY:
        return reporter.generate_daily_report(anchor_date)
    if report_kind == REPORT_KIND_WEEKLY:
        return reporter.generate_weekly_report(anchor_date)
    if report_kind == REPORT_KIND_MONTHLY:
        return reporter.generate_monthly_report(anchor_date)
    raise ValueError(f"Unsupported report kind: {report_kind}")


def deliver_report(
    reporter: DailyReporter,
    emailer: ReportEmailer,
    report_kind: str,
    anchor_date: str,
    logger: logging.Logger | None = None,
    receipts_path: Path | None = None,
) -> Path:
    period_key = get_period_key(report_kind, anchor_date)
    report_path = generate_report_for_period(reporter, report_kind, anchor_date)
    if should_email_report(report_kind, period_key, receipts_path):
        if emailer.is_configured():
            emailer.send_report(report_path)
            mark_report_emailed(report_kind, period_key, receipts_path)
        elif logger is not None:
            logger.info("Email delivery disabled; generated %s report for %s.", report_kind, period_key)
    elif logger is not None and emailer.is_configured():
        logger.info("Skipping duplicate email delivery for %s period: %s", report_kind, period_key)
    return report_path


def get_latest_due_daily_date(now: datetime, delivery_time: time_value) -> str:
    target_date = now.date() - timedelta(days=1)
    if now.time() < delivery_time:
        target_date -= timedelta(days=1)
    return target_date.isoformat()


def get_latest_due_week_anchor(now: datetime, delivery_time: time_value) -> str:
    current_week_start = now.date() - timedelta(days=now.weekday())
    if now.weekday() == 0 and now.time() < delivery_time:
        current_week_start -= timedelta(days=7)
    latest_due_week_start = current_week_start - timedelta(days=7)
    return latest_due_week_start.isoformat()


def get_latest_due_month_anchor(now: datetime, delivery_time: time_value) -> str:
    current_month_start = now.date().replace(day=1)
    if now.day == 1 and now.time() < delivery_time:
        current_month_start = (current_month_start - timedelta(days=1)).replace(day=1)
    latest_due_month_end = current_month_start - timedelta(days=1)
    return latest_due_month_end.isoformat()


def get_latest_due_periods(now: datetime, delivery_time: time_value) -> list[tuple[str, str]]:
    return [
        (REPORT_KIND_DAILY, get_latest_due_daily_date(now, delivery_time)),
        (REPORT_KIND_WEEKLY, get_latest_due_week_anchor(now, delivery_time)),
        (REPORT_KIND_MONTHLY, get_latest_due_month_anchor(now, delivery_time)),
    ]


def deliver_due_reports(
    reporter: DailyReporter,
    emailer: ReportEmailer,
    logger: logging.Logger,
    delivery_time: time_value,
    *,
    now: datetime | None = None,
    receipts_path: Path | None = None,
) -> list[tuple[str, str, Path]]:
    current_time = now or datetime.now()
    delivered_reports: list[tuple[str, str, Path]] = []

    for report_kind, anchor_date in get_latest_due_periods(current_time, delivery_time):
        period_key = get_period_key(report_kind, anchor_date)
        if not should_email_report(report_kind, period_key, receipts_path) and emailer.is_configured():
            continue

        report_path = deliver_report(
            reporter,
            emailer,
            report_kind,
            anchor_date,
            logger,
            receipts_path,
        )
        delivered_reports.append((report_kind, period_key, report_path))

        if emailer.is_configured() and should_email_report(report_kind, period_key, receipts_path) is False:
            logger.info("Report emailed to: %s", emailer.settings.recipient)
        logger.info("Report written to: %s", report_path)

    return delivered_reports


def run_retention_cleanup(
    database: FocusDatabase,
    retention_days: int,
    logger: logging.Logger,
    *,
    now: datetime | None = None,
) -> int:
    cutoff = (now or datetime.now()) - timedelta(days=retention_days)
    deleted_rows = database.purge_activity_before(cutoff)
    if deleted_rows > 0:
        logger.info("Purged %s raw activity rows older than %s days.", deleted_rows, retention_days)
    return deleted_rows


def schedule_report_delivery(
    reporter: DailyReporter,
    emailer: ReportEmailer,
    database: FocusDatabase,
    time_str: str,
    retention_days: int,
) -> None:
    import schedule

    logger = logging.getLogger(LOGGER_NAME)

    delivery_time = parse_scheduled_delivery_time(time_str)

    def _run_report() -> None:
        try:
            deliver_due_reports(reporter, emailer, logger, delivery_time)
            run_retention_cleanup(database, retention_days, logger)
        except Exception as exc:
            logger.warning("Scheduled report delivery failed: %s", exc, exc_info=True)

    schedule.every().day.at(time_str).do(_run_report)


def main() -> None:
    import schedule

    logger = configure_logging()
    load_env_file()
    validate_runtime_environment()
    settings = load_settings()

    database = FocusDatabase(DATABASE_PATH)
    database.initialize()
    ensure_default_goals(database, settings)

    classifier = build_classification_engine(settings, database)
    reporter = DailyReporter(database=database, reports_dir=REPORTS_DIR)
    emailer = build_emailer(settings)
    coach = AdaptiveCoach(
        database=database,
        settings=build_agent_settings(settings),
        notifier=DesktopNotifier(),
    )

    delivery_time = parse_scheduled_delivery_time(str(settings["scheduled_delivery_time"]))
    deliver_due_reports(reporter, emailer, logger, delivery_time)
    run_retention_cleanup(database, int(settings["raw_activity_retention_days"]), logger)
    schedule_report_delivery(
        reporter,
        emailer,
        database,
        settings["scheduled_delivery_time"],
        int(settings["raw_activity_retention_days"]),
    )

    interval_seconds = int(settings["tracking_interval_seconds"])
    logger.info("Focus Tracker Agent running. Press Ctrl+C to stop.")

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
                    classification_confidence=classification.confidence,
                    classification_source=classification.source,
                    classification_provisional=classification.provisional,
                    classification_reason=classification.reason,
                    classification_fingerprint=classification.fingerprint,
                )
                coach.tick()
            except Exception as exc:
                logger.warning("Tracking iteration failed: %s", exc, exc_info=True)
            finally:
                schedule.run_pending()
                elapsed = time.monotonic() - loop_started
                sleep_for = max(0.0, interval_seconds - elapsed)
                time.sleep(sleep_for)
    except KeyboardInterrupt:
        logger.info("Stopping tracker.")
    finally:
        logger.info("Focus Tracker Agent stopped.")


if __name__ == "__main__":
    main()
