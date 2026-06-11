from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as time_value, timedelta
from typing import Any
import json

from database import FocusDatabase, count_switches


SESSION_DEEP_WORK = "deep_work"
SESSION_FRAGMENTED = "fragmented"
SESSION_DRIFTING = "drifting"
SESSION_BREAK_DUE = "break_due"
SESSION_NEUTRAL_ADMIN = "neutral_admin"

ACTION_ENCOURAGE_FOCUS = "encourage_focus"
ACTION_WARN_DRIFT = "warn_drift"
ACTION_SUGGEST_BREAK = "suggest_break"
ACTION_GOAL_REMINDER = "goal_reminder"
ACTION_STAY_SILENT = "stay_silent"

OUTCOME_PENDING = "pending"
OUTCOME_SUCCESS = "success"
OUTCOME_PARTIAL = "partial"
OUTCOME_FAILURE = "failure"

GOAL_TYPE_DAILY_PRODUCTIVE_MINUTES = "daily_productive_minutes"
GOAL_TYPE_DISTRACTING_LIMIT = "distracting_limit"
GOAL_TYPE_FOCUS_BLOCK_COUNT = "focus_block_count"


@dataclass(frozen=True)
class GoalRecord:
    goal_id: int
    goal_type: str
    name: str
    target_value: int
    window_minutes: int
    active: bool
    schedule_start: str
    schedule_end: str
    days_of_week: str
    config_json: str


@dataclass(frozen=True)
class GoalEvaluation:
    goal_id: int
    goal_name: str
    goal_type: str
    status: str
    progress_value: int
    target_value: int
    detail: str
    at_risk: bool


@dataclass(frozen=True)
class SessionStateSnapshot:
    state: str
    productive_streak_seconds: int
    switch_count: int
    distraction_ratio: float
    productive_ratio: float
    recent_rows: list[dict[str, Any]]
    detail: str


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    message: str
    reason: str
    goal_id: int | None = None


@dataclass(frozen=True)
class AgentSettings:
    enabled: bool
    default_mode: str
    productive_window_minutes: int
    break_threshold_minutes: int
    drifting_window_minutes: int
    distracting_ratio_threshold: float
    fragmented_window_minutes: int
    fragmented_switch_threshold: int
    neutral_window_minutes: int
    intervention_cooldowns: dict[str, int]
    outcome_window_minutes: int


def agent_settings_from_dict(payload: dict[str, Any]) -> AgentSettings:
    policy = payload.get("policy", {}) if isinstance(payload, dict) else {}
    goal_defaults = payload.get("goal_defaults", {}) if isinstance(payload, dict) else {}
    cooldowns = payload.get("intervention_cooldowns", {}) if isinstance(payload, dict) else {}
    return AgentSettings(
        enabled=bool(payload.get("enabled", False)),
        default_mode=str(payload.get("default_mode", "adaptive_coach")),
        productive_window_minutes=max(5, int(policy.get("productive_window_minutes", 60))),
        break_threshold_minutes=max(10, int(policy.get("break_threshold_minutes", 45))),
        drifting_window_minutes=max(5, int(policy.get("drifting_window_minutes", 30))),
        distracting_ratio_threshold=max(0.1, min(1.0, float(policy.get("distracting_ratio_threshold", 0.5)))),
        fragmented_window_minutes=max(5, int(policy.get("fragmented_window_minutes", 10))),
        fragmented_switch_threshold=max(2, int(policy.get("fragmented_switch_threshold", 20))),
        neutral_window_minutes=max(5, int(policy.get("neutral_window_minutes", 20))),
        intervention_cooldowns={
            ACTION_ENCOURAGE_FOCUS: max(1, int(cooldowns.get(ACTION_ENCOURAGE_FOCUS, 45))),
            ACTION_WARN_DRIFT: max(1, int(cooldowns.get(ACTION_WARN_DRIFT, 30))),
            ACTION_SUGGEST_BREAK: max(1, int(cooldowns.get(ACTION_SUGGEST_BREAK, 60))),
            ACTION_GOAL_REMINDER: max(1, int(cooldowns.get(ACTION_GOAL_REMINDER, 90))),
        },
        outcome_window_minutes=max(5, int(payload.get("outcome_window_minutes", 15))),
    )


def _parse_days_of_week(raw_value: str) -> set[int]:
    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return set(range(7))
    if not isinstance(parsed, list):
        return set(range(7))
    days: set[int] = set()
    for item in parsed:
        try:
            day = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6:
            days.add(day)
    return days or set(range(7))


def _parse_goal_config(raw_value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_hhmm(raw_value: str, default: str) -> time_value:
    value = raw_value or default
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return datetime.strptime(default, "%H:%M").time()


def _goal_is_scheduled(goal: GoalRecord, now: datetime) -> bool:
    if now.weekday() not in _parse_days_of_week(goal.days_of_week):
        return False
    start_time = _parse_hhmm(goal.schedule_start, "00:00")
    end_time = _parse_hhmm(goal.schedule_end, "23:59")
    current_time = now.time()
    if start_time <= end_time:
        return start_time <= current_time <= end_time
    return current_time >= start_time or current_time <= end_time


class SessionStateEvaluator:
    def __init__(self, database: FocusDatabase, settings: AgentSettings) -> None:
        self.database = database
        self.settings = settings

    def evaluate(self, now: datetime | None = None) -> SessionStateSnapshot:
        current_time = now or datetime.now()
        drifting_rows = self.database.get_recent_activity(minutes=self.settings.drifting_window_minutes, now=current_time)
        drifting_total = sum(int(row.get("duration_seconds") or 0) for row in drifting_rows)
        distracting_seconds = sum(
            int(row.get("duration_seconds") or 0)
            for row in drifting_rows
            if str(row.get("category") or "").lower() == "distracting"
        )
        neutral_seconds = sum(
            int(row.get("duration_seconds") or 0)
            for row in drifting_rows
            if str(row.get("category") or "").lower() == "neutral"
        )
        productive_seconds = sum(
            int(row.get("duration_seconds") or 0)
            for row in drifting_rows
            if str(row.get("category") or "").lower() == "productive"
        )
        distraction_ratio = (distracting_seconds / drifting_total) if drifting_total else 0.0
        productive_ratio = (productive_seconds / drifting_total) if drifting_total else 0.0
        productive_streak = self.database.get_current_productive_streak_seconds(
            max_minutes=max(self.settings.break_threshold_minutes + 30, self.settings.productive_window_minutes),
            now=current_time,
        )
        switch_count = self.database.get_recent_switch_count(
            minutes=self.settings.fragmented_window_minutes,
            now=current_time,
        )

        if productive_streak >= self.settings.break_threshold_minutes * 60:
            return SessionStateSnapshot(
                state=SESSION_BREAK_DUE,
                productive_streak_seconds=productive_streak,
                switch_count=switch_count,
                distraction_ratio=distraction_ratio,
                productive_ratio=productive_ratio,
                recent_rows=drifting_rows,
                detail="Long productive streak suggests a break is due.",
            )
        if productive_streak >= self.settings.productive_window_minutes * 60:
            return SessionStateSnapshot(
                state=SESSION_DEEP_WORK,
                productive_streak_seconds=productive_streak,
                switch_count=switch_count,
                distraction_ratio=distraction_ratio,
                productive_ratio=productive_ratio,
                recent_rows=drifting_rows,
                detail="Recent activity shows sustained productive work.",
            )
        if distraction_ratio >= self.settings.distracting_ratio_threshold and drifting_total > 0:
            return SessionStateSnapshot(
                state=SESSION_DRIFTING,
                productive_streak_seconds=productive_streak,
                switch_count=switch_count,
                distraction_ratio=distraction_ratio,
                productive_ratio=productive_ratio,
                recent_rows=drifting_rows,
                detail="Recent activity is distraction-heavy.",
            )
        if switch_count >= self.settings.fragmented_switch_threshold:
            return SessionStateSnapshot(
                state=SESSION_FRAGMENTED,
                productive_streak_seconds=productive_streak,
                switch_count=switch_count,
                distraction_ratio=distraction_ratio,
                productive_ratio=productive_ratio,
                recent_rows=drifting_rows,
                detail="Recent activity shows frequent app switching.",
            )
        neutral_threshold_seconds = self.settings.neutral_window_minutes * 60
        if neutral_seconds >= neutral_threshold_seconds and productive_seconds == 0 and distracting_seconds == 0:
            return SessionStateSnapshot(
                state=SESSION_NEUTRAL_ADMIN,
                productive_streak_seconds=productive_streak,
                switch_count=switch_count,
                distraction_ratio=distraction_ratio,
                productive_ratio=productive_ratio,
                recent_rows=drifting_rows,
                detail="Recent activity is mostly neutral admin work.",
            )
        return SessionStateSnapshot(
            state=SESSION_NEUTRAL_ADMIN,
            productive_streak_seconds=productive_streak,
            switch_count=switch_count,
            distraction_ratio=distraction_ratio,
            productive_ratio=productive_ratio,
            recent_rows=drifting_rows,
            detail="No strong session pattern is active.",
        )


class GoalService:
    def __init__(self, database: FocusDatabase) -> None:
        self.database = database

    def active_goals(self, now: datetime | None = None) -> list[GoalRecord]:
        current_time = now or datetime.now()
        goals = [self._goal_from_row(row) for row in self.database.list_goals(active_only=True)]
        return [goal for goal in goals if _goal_is_scheduled(goal, current_time)]

    def evaluate_goals(self, now: datetime | None = None) -> list[GoalEvaluation]:
        current_time = now or datetime.now()
        evaluations: list[GoalEvaluation] = []
        for goal in self.active_goals(current_time):
            evaluation = self._evaluate_goal(goal, current_time)
            self.database.record_goal_evaluation(
                goal_id=evaluation.goal_id,
                timestamp=current_time.replace(microsecond=0).isoformat(),
                status=evaluation.status,
                progress_value=evaluation.progress_value,
                target_value=evaluation.target_value,
                detail=evaluation.detail,
                at_risk=evaluation.at_risk,
            )
            evaluations.append(evaluation)
        return evaluations

    def _goal_from_row(self, row: dict[str, Any]) -> GoalRecord:
        return GoalRecord(
            goal_id=int(row["id"]),
            goal_type=str(row.get("goal_type") or ""),
            name=str(row.get("name") or ""),
            target_value=int(row.get("target_value") or 0),
            window_minutes=int(row.get("window_minutes") or 0),
            active=bool(row.get("active")),
            schedule_start=str(row.get("schedule_start") or "00:00"),
            schedule_end=str(row.get("schedule_end") or "23:59"),
            days_of_week=str(row.get("days_of_week") or "[0,1,2,3,4,5,6]"),
            config_json=str(row.get("config_json") or "{}"),
        )

    def _evaluate_goal(self, goal: GoalRecord, now: datetime) -> GoalEvaluation:
        config = _parse_goal_config(goal.config_json)
        if goal.goal_type == GOAL_TYPE_DAILY_PRODUCTIVE_MINUTES:
            rows = self.database.query_activity_for_date(now.date().isoformat())
            productive_seconds = sum(
                int(row.get("duration_seconds") or 0)
                for row in rows
                if str(row.get("category") or "").lower() == "productive"
            )
            progress_minutes = productive_seconds // 60
            status = "met" if progress_minutes >= goal.target_value else "in_progress"
            at_risk = progress_minutes < goal.target_value and now.time() >= _parse_hhmm(goal.schedule_end, "23:59")
            detail = f"Productive minutes today: {progress_minutes}/{goal.target_value}."
            return GoalEvaluation(goal.goal_id, goal.name, goal.goal_type, status, progress_minutes, goal.target_value, detail, at_risk)

        if goal.goal_type == GOAL_TYPE_DISTRACTING_LIMIT:
            rows = self.database.get_recent_activity(minutes=goal.window_minutes or 60, now=now)
            blocked_apps = {str(item).lower() for item in config.get("blocked_apps", []) if str(item).strip()}
            blocked_sites = {str(item).lower() for item in config.get("blocked_sites", []) if str(item).strip()}
            distracting_seconds = 0
            for row in rows:
                category = str(row.get("category") or "").lower()
                if category != "distracting":
                    continue
                app_name = str(row.get("app_name") or "").lower()
                site_hint = str(row.get("site_hint") or "").lower()
                if blocked_apps and app_name in blocked_apps:
                    distracting_seconds += int(row.get("duration_seconds") or 0)
                elif blocked_sites and site_hint in blocked_sites:
                    distracting_seconds += int(row.get("duration_seconds") or 0)
                elif not blocked_apps and not blocked_sites:
                    distracting_seconds += int(row.get("duration_seconds") or 0)
            progress_minutes = distracting_seconds // 60
            status = "met" if progress_minutes <= goal.target_value else "missed"
            at_risk = progress_minutes >= max(goal.target_value - 5, 0)
            detail = f"Distracting minutes in the last {goal.window_minutes or 60} minutes: {progress_minutes}/{goal.target_value}."
            return GoalEvaluation(goal.goal_id, goal.name, goal.goal_type, status, progress_minutes, goal.target_value, detail, at_risk)

        if goal.goal_type == GOAL_TYPE_FOCUS_BLOCK_COUNT:
            blocks = self.database.count_focus_blocks_for_date(
                goal_date=now.date().isoformat(),
                minimum_duration_seconds=(goal.window_minutes or 25) * 60,
            )
            status = "met" if blocks >= goal.target_value else "in_progress"
            at_risk = blocks < goal.target_value and now.time() >= _parse_hhmm(goal.schedule_end, "23:59")
            detail = f"Meaningful focus blocks today: {blocks}/{goal.target_value}."
            return GoalEvaluation(goal.goal_id, goal.name, goal.goal_type, status, blocks, goal.target_value, detail, at_risk)

        return GoalEvaluation(goal.goal_id, goal.name, goal.goal_type, "unknown", 0, goal.target_value, "Unsupported goal type.", False)


class OutcomeEvaluator:
    def __init__(self, database: FocusDatabase, settings: AgentSettings) -> None:
        self.database = database
        self.settings = settings

    def evaluate_pending(self, now: datetime | None = None) -> list[dict[str, Any]]:
        current_time = now or datetime.now()
        pending_rows = self.database.list_pending_interventions()
        finalized: list[dict[str, Any]] = []
        for row in pending_rows:
            started_at = datetime.fromisoformat(str(row["timestamp"]))
            if current_time < started_at + timedelta(minutes=self.settings.outcome_window_minutes):
                continue
            outcome = self._score_outcome(row, started_at)
            self.database.record_intervention_outcome(
                intervention_id=int(row["id"]),
                timestamp=current_time.replace(microsecond=0).isoformat(),
                outcome_status=outcome["outcome_status"],
                productive_recovered=bool(outcome["productive_recovered"]),
                distraction_ratio_before=float(outcome["distraction_ratio_before"]),
                distraction_ratio_after=float(outcome["distraction_ratio_after"]),
                switch_count_before=int(outcome["switch_count_before"]),
                switch_count_after=int(outcome["switch_count_after"]),
                notes=str(outcome["notes"]),
            )
            finalized.append(outcome)
        return finalized

    def _score_outcome(self, intervention_row: dict[str, Any], started_at: datetime) -> dict[str, Any]:
        before_rows = self.database.query_activity_between(
            started_at - timedelta(minutes=self.settings.outcome_window_minutes),
            started_at,
        )
        after_rows = self.database.query_activity_between(
            started_at,
            started_at + timedelta(minutes=self.settings.outcome_window_minutes),
        )
        before_total = sum(int(row.get("duration_seconds") or 0) for row in before_rows)
        after_total = sum(int(row.get("duration_seconds") or 0) for row in after_rows)
        before_distracting = sum(
            int(row.get("duration_seconds") or 0)
            for row in before_rows
            if str(row.get("category") or "").lower() == "distracting"
        )
        after_distracting = sum(
            int(row.get("duration_seconds") or 0)
            for row in after_rows
            if str(row.get("category") or "").lower() == "distracting"
        )
        after_productive = sum(
            int(row.get("duration_seconds") or 0)
            for row in after_rows
            if str(row.get("category") or "").lower() == "productive"
        )
        productive_recovered = after_productive > 0
        distraction_ratio_before = (before_distracting / before_total) if before_total else 0.0
        distraction_ratio_after = (after_distracting / after_total) if after_total else 0.0
        switch_count_before = count_switches(before_rows)
        switch_count_after = count_switches(after_rows)
        if productive_recovered:
            outcome_status = OUTCOME_SUCCESS
            notes = "Productive activity resumed within the outcome window."
        elif distraction_ratio_after < distraction_ratio_before or switch_count_after < switch_count_before:
            outcome_status = OUTCOME_PARTIAL
            notes = "Behavior improved but did not fully recover to productive work."
        else:
            outcome_status = OUTCOME_FAILURE
            notes = "Distraction or fragmentation persisted after the intervention."
        return {
            "intervention_id": int(intervention_row["id"]),
            "outcome_status": outcome_status,
            "productive_recovered": productive_recovered,
            "distraction_ratio_before": distraction_ratio_before,
            "distraction_ratio_after": distraction_ratio_after,
            "switch_count_before": switch_count_before,
            "switch_count_after": switch_count_after,
            "notes": notes,
        }


class AdaptiveCoachPolicy:
    def __init__(self, database: FocusDatabase, settings: AgentSettings) -> None:
        self.database = database
        self.settings = settings

    def choose_action(
        self,
        *,
        session_state: SessionStateSnapshot,
        goal_evaluations: list[GoalEvaluation],
        now: datetime | None = None,
    ) -> PolicyDecision:
        current_time = now or datetime.now()
        at_risk_goal = next((goal for goal in goal_evaluations if goal.at_risk), None)
        if session_state.state == SESSION_DEEP_WORK:
            return PolicyDecision(ACTION_STAY_SILENT, "", "Deep work is active, so the coach stays quiet.")
        if session_state.state == SESSION_BREAK_DUE and self._action_available(ACTION_SUGGEST_BREAK, current_time):
            return PolicyDecision(
                ACTION_SUGGEST_BREAK,
                "You have been in a strong focus streak. A short break now may help sustain your next block.",
                session_state.detail,
            )
        if session_state.state == SESSION_DRIFTING and self._action_available(ACTION_WARN_DRIFT, current_time):
            return PolicyDecision(
                ACTION_WARN_DRIFT,
                "Recent activity looks distraction-heavy. Return to your current task and protect the next 10 minutes.",
                session_state.detail,
            )
        if session_state.state == SESSION_FRAGMENTED and self._action_available(ACTION_ENCOURAGE_FOCUS, current_time):
            return PolicyDecision(
                ACTION_ENCOURAGE_FOCUS,
                "Recent switching is high. Pick one task and stay in one window for the next focus block.",
                session_state.detail,
            )
        if at_risk_goal is not None and self._goal_reminder_allowed(current_time):
            return PolicyDecision(
                ACTION_GOAL_REMINDER,
                f"Goal at risk: {at_risk_goal.detail}",
                "An active goal needs a reminder based on current progress.",
                goal_id=at_risk_goal.goal_id,
            )
        return PolicyDecision(ACTION_STAY_SILENT, "", "No intervention is appropriate right now.")

    def _action_available(self, action: str, now: datetime) -> bool:
        last_sent = self.database.get_recent_intervention_timestamp(action)
        if last_sent is None:
            return True
        cooldown_minutes = self.settings.intervention_cooldowns.get(action, 30)
        return (now - last_sent) >= timedelta(minutes=cooldown_minutes)

    def _goal_reminder_allowed(self, now: datetime) -> bool:
        if not self._action_available(ACTION_GOAL_REMINDER, now):
            return False
        stats = self.database.get_intervention_effectiveness_stats(action=ACTION_GOAL_REMINDER)
        if stats["total"] == 0:
            return False
        return stats["success"] + stats["partial"] >= stats["failure"]


class AdaptiveCoach:
    def __init__(self, database: FocusDatabase, settings: AgentSettings, notifier: Any) -> None:
        self.database = database
        self.settings = settings
        self.notifier = notifier
        self.session_evaluator = SessionStateEvaluator(database, settings)
        self.goal_service = GoalService(database)
        self.policy = AdaptiveCoachPolicy(database, settings)
        self.outcomes = OutcomeEvaluator(database, settings)

    def tick(self, now: datetime | None = None) -> PolicyDecision | None:
        current_time = now or datetime.now()
        self.outcomes.evaluate_pending(current_time)
        if not self.settings.enabled:
            return None
        session_state = self.session_evaluator.evaluate(current_time)
        goal_evaluations = self.goal_service.evaluate_goals(current_time)
        decision = self.policy.choose_action(
            session_state=session_state,
            goal_evaluations=goal_evaluations,
            now=current_time,
        )
        self.database.insert_session_state_snapshot(
            timestamp=current_time.replace(microsecond=0).isoformat(),
            session_state=session_state.state,
            productive_streak_seconds=session_state.productive_streak_seconds,
            switch_count=session_state.switch_count,
            distraction_ratio=session_state.distraction_ratio,
            productive_ratio=session_state.productive_ratio,
            detail=session_state.detail,
        )
        if decision.action == ACTION_STAY_SILENT:
            return decision
        self.notifier.notify("Focus Tracker", decision.message)
        self.database.insert_intervention(
            timestamp=current_time.replace(microsecond=0).isoformat(),
            action=decision.action,
            message=decision.message,
            reason=decision.reason,
            session_state=session_state.state,
            goal_id=decision.goal_id,
        )
        return decision
