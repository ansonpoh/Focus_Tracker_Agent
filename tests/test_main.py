import os

from main import DEFAULT_SETTINGS, build_emailer, build_nudge_config, load_env_file, load_settings_from_data


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
