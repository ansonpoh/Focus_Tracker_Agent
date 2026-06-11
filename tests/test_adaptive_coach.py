from datetime import datetime

from adaptive_coach import (
    ACTION_GOAL_REMINDER,
    ACTION_STAY_SILENT,
    ACTION_WARN_DRIFT,
    AdaptiveCoachPolicy,
    GoalService,
    SessionStateEvaluator,
    agent_settings_from_dict,
)
from database import FocusDatabase


def build_database(tmp_path):
    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()
    return database


def test_daily_productive_goal_evaluation(tmp_path) -> None:
    database = build_database(tmp_path)
    database.upsert_goal(
        goal_type="daily_productive_minutes",
        name="Daily productive minutes",
        target_value=60,
        window_minutes=0,
        schedule_start="08:00",
        schedule_end="18:00",
        days_of_week=[1],
        config={},
    )
    database.insert_activity(
        timestamp="2026-06-09T09:00:00",
        app_name="Code.exe",
        window_title="Editor",
        category="productive",
        duration_seconds=3600,
    )

    evaluations = GoalService(database).evaluate_goals(datetime(2026, 6, 9, 10, 0, 0))

    assert evaluations[0].status == "met"
    assert evaluations[0].progress_value == 60


def test_session_state_detects_drifting(tmp_path) -> None:
    database = build_database(tmp_path)
    settings = agent_settings_from_dict(
        {
            "enabled": True,
            "policy": {
                "productive_window_minutes": 30,
                "break_threshold_minutes": 45,
                "drifting_window_minutes": 30,
                "distracting_ratio_threshold": 0.5,
                "fragmented_window_minutes": 10,
                "fragmented_switch_threshold": 20,
                "neutral_window_minutes": 20,
            },
            "intervention_cooldowns": {},
            "outcome_window_minutes": 15,
        }
    )
    database.insert_activity(
        timestamp="2026-06-09T09:00:00",
        app_name="chrome.exe",
        window_title="YouTube",
        category="distracting",
        duration_seconds=1200,
    )
    database.insert_activity(
        timestamp="2026-06-09T09:20:00",
        app_name="chrome.exe",
        window_title="Reddit",
        category="distracting",
        duration_seconds=600,
    )

    snapshot = SessionStateEvaluator(database, settings).evaluate(datetime(2026, 6, 9, 9, 29, 0))

    assert snapshot.state == "drifting"


def test_policy_prefers_silence_for_deep_work(tmp_path) -> None:
    database = build_database(tmp_path)
    settings = agent_settings_from_dict({"enabled": True, "intervention_cooldowns": {}, "policy": {}, "outcome_window_minutes": 15})

    decision = AdaptiveCoachPolicy(database, settings).choose_action(
        session_state=type("State", (), {"state": "deep_work", "detail": "focused"})(),
        goal_evaluations=[],
        now=datetime(2026, 6, 9, 10, 0, 0),
    )

    assert decision.action == ACTION_STAY_SILENT


def test_policy_warns_on_drifting_without_cooldown(tmp_path) -> None:
    database = build_database(tmp_path)
    settings = agent_settings_from_dict({"enabled": True, "intervention_cooldowns": {}, "policy": {}, "outcome_window_minutes": 15})

    decision = AdaptiveCoachPolicy(database, settings).choose_action(
        session_state=type("State", (), {"state": "drifting", "detail": "Recent activity is distraction-heavy."})(),
        goal_evaluations=[],
        now=datetime(2026, 6, 9, 10, 0, 0),
    )

    assert decision.action == ACTION_WARN_DRIFT


def test_policy_uses_goal_reminder_when_history_is_positive(tmp_path) -> None:
    database = build_database(tmp_path)
    settings = agent_settings_from_dict({"enabled": True, "intervention_cooldowns": {}, "policy": {}, "outcome_window_minutes": 15})
    intervention_id = database.insert_intervention(
        timestamp="2026-06-09T08:00:00",
        action="goal_reminder",
        message="Goal at risk.",
        reason="Goal reminder",
        session_state="neutral_admin",
        goal_id=None,
    )
    database.record_intervention_outcome(
        intervention_id=intervention_id,
        timestamp="2026-06-09T08:20:00",
        outcome_status="success",
        productive_recovered=True,
        distraction_ratio_before=0.2,
        distraction_ratio_after=0.0,
        switch_count_before=4,
        switch_count_after=1,
        notes="Recovered.",
    )

    evaluation = type("GoalEvaluation", (), {"at_risk": True, "detail": "Productive minutes today: 40/120.", "goal_id": 1})()
    decision = AdaptiveCoachPolicy(database, settings).choose_action(
        session_state=type("State", (), {"state": "neutral_admin", "detail": "No strong pattern."})(),
        goal_evaluations=[evaluation],
        now=datetime(2026, 6, 9, 12, 0, 0),
    )

    assert decision.action == ACTION_GOAL_REMINDER
