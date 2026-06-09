from main import DEFAULT_SETTINGS, build_nudge_config, load_settings_from_data


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
