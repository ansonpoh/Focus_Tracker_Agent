from datetime import datetime

from nudger import NudgeConfig, Nudger


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def notify(self, title: str, message: str) -> None:
        self.messages.append((title, message))


class FakeDatabase:
    def __init__(
        self,
        *,
        recent_activity: list[dict] | None = None,
        switch_count: int = 0,
        productive_streak: int = 0,
        recent_nudges: dict[str, datetime] | None = None,
    ) -> None:
        self.recent_activity = recent_activity or []
        self.switch_count = switch_count
        self.productive_streak = productive_streak
        self.recent_nudges = recent_nudges or {}
        self.inserted_nudges: list[dict] = []

    def get_recent_activity(self, *, minutes: int, now: datetime, extra_buffer_minutes: int = 0) -> list[dict]:
        return self.recent_activity

    def get_recent_switch_count(self, *, minutes: int, now: datetime) -> int:
        return self.switch_count

    def get_current_productive_streak_seconds(self, *, max_minutes: int, now: datetime) -> int:
        return self.productive_streak

    def get_recent_nudge_timestamp(self, nudge_type: str) -> datetime | None:
        return self.recent_nudges.get(nudge_type)

    def insert_nudge(self, *, timestamp: str, nudge_type: str, message: str) -> None:
        self.inserted_nudges.append(
            {
                "timestamp": timestamp,
                "nudge_type": nudge_type,
                "message": message,
            }
        )


def test_nudger_uses_config_values_in_messages() -> None:
    database = FakeDatabase(
        recent_activity=[
            {"category": "distracting", "duration_seconds": 15 * 60},
        ]
    )
    notifier = FakeNotifier()
    config = NudgeConfig(
        distracting_minutes_threshold=15,
        distracting_window_minutes=25,
        switch_threshold=8,
        switch_window_minutes=5,
        productive_minutes_threshold=40,
        cooldown_minutes=30,
    )
    nudger = Nudger(database=database, notifier=notifier, config=config)

    result = nudger.check_nudges(now=datetime(2026, 6, 9, 10, 0, 0))

    assert result == "distracting"
    assert "15+ minutes" in notifier.messages[0][1]
    assert "last 25 minutes" in notifier.messages[0][1]


def test_nudger_records_switch_message_with_current_thresholds() -> None:
    database = FakeDatabase(switch_count=8)
    notifier = FakeNotifier()
    config = NudgeConfig(
        distracting_minutes_threshold=15,
        distracting_window_minutes=25,
        switch_threshold=8,
        switch_window_minutes=5,
        productive_minutes_threshold=40,
        cooldown_minutes=30,
    )
    nudger = Nudger(database=database, notifier=notifier, config=config)

    result = nudger.check_nudges(now=datetime(2026, 6, 9, 11, 0, 0))

    assert result == "switches"
    assert "last 5 minutes" in notifier.messages[0][1]


def test_nudger_records_productive_break_message_with_current_threshold() -> None:
    database = FakeDatabase(productive_streak=40 * 60)
    notifier = FakeNotifier()
    config = NudgeConfig(productive_minutes_threshold=40, cooldown_minutes=30)
    nudger = Nudger(database=database, notifier=notifier, config=config)

    result = nudger.check_nudges(now=datetime(2026, 6, 9, 12, 0, 0))

    assert result == "productive_break"
    assert "40 minutes" in notifier.messages[0][1]
