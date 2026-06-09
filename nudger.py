from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import sys

from database import FocusDatabase


class DesktopNotifier:
    def notify(self, title: str, message: str) -> None:
        """
        Send a desktop notification if a supported backend is available.

        The agent should keep running even when notifications are unavailable,
        so all backend failures fall back to stdout.
        """

        sent = False
        try:
            if sys.platform == "win32":
                try:
                    from winotify import Notification, audio

                    toast = Notification(
                        app_id="Focus Tracker Agent",
                        title=title,
                        msg=message,
                        duration="short",
                    )
                    toast.set_audio(audio.Default, loop=False)
                    toast.show()
                    sent = True
                    return
                except Exception:
                    pass

            try:
                from plyer import notification

                notification.notify(title=title, message=message, app_name="Focus Tracker Agent")
                sent = True
                return
            except Exception:
                pass

        finally:
            if not sent:
                print(f"[nudge] {title}: {message}")


@dataclass
class NudgeConfig:
    distracting_minutes_threshold: int = 20
    distracting_window_minutes: int = 30
    switch_threshold: int = 20
    switch_window_minutes: int = 10
    productive_minutes_threshold: int = 45
    cooldown_minutes: int = 30


class Nudger:
    def __init__(
        self,
        database: FocusDatabase,
        notifier: DesktopNotifier | None = None,
        config: NudgeConfig | None = None,
    ) -> None:
        self.database = database
        self.notifier = notifier or DesktopNotifier()
        self.config = config or NudgeConfig()

    def _distracting_message(self) -> str:
        return (
            f"You've spent {self.config.distracting_minutes_threshold}+ minutes on distracting apps/sites "
            f"in the last {self.config.distracting_window_minutes} minutes. Return to your current task?"
        )

    def _switches_message(self) -> str:
        return (
            f"You've switched apps frequently in the last {self.config.switch_window_minutes} minutes. "
            "Try one 25-minute focus block."
        )

    def _productive_break_message(self) -> str:
        return f"You've focused for {self.config.productive_minutes_threshold} minutes. Consider taking a short break."

    def _cooldown_active(self, nudge_type: str, now: datetime) -> bool:
        last_sent = self.database.get_recent_nudge_timestamp(nudge_type)
        if last_sent is None:
            return False
        return (now - last_sent) < timedelta(minutes=self.config.cooldown_minutes)

    def _send_nudge(self, nudge_type: str, message: str, now: datetime) -> None:
        try:
            self.notifier.notify("Focus Tracker", message)
        finally:
            self.database.insert_nudge(
                timestamp=now.replace(microsecond=0).isoformat(),
                nudge_type=nudge_type,
                message=message,
            )

    def check_nudges(self, now: datetime | None = None) -> str | None:
        current_time = now or datetime.now()

        distracting_minutes = self.database.get_recent_activity(
            minutes=self.config.distracting_window_minutes,
            now=current_time,
        )
        distracting_seconds = sum(
            int(row.get("duration_seconds") or 0)
            for row in distracting_minutes
            if str(row.get("category") or "").lower() == "distracting"
        )
        if (
            distracting_seconds >= self.config.distracting_minutes_threshold * 60
            and not self._cooldown_active("distracting", current_time)
        ):
            message = self._distracting_message()
            self._send_nudge("distracting", message, current_time)
            return "distracting"

        switch_count = self.database.get_recent_switch_count(
            minutes=self.config.switch_window_minutes,
            now=current_time,
        )
        if switch_count >= self.config.switch_threshold and not self._cooldown_active("switches", current_time):
            message = self._switches_message()
            self._send_nudge("switches", message, current_time)
            return "switches"

        productive_streak = self.database.get_current_productive_streak_seconds(
            max_minutes=max(self.config.productive_minutes_threshold + 30, 90),
            now=current_time,
        )
        if (
            productive_streak >= self.config.productive_minutes_threshold * 60
            and not self._cooldown_active("productive_break", current_time)
        ):
            message = self._productive_break_message()
            self._send_nudge("productive_break", message, current_time)
            return "productive_break"

        return None
