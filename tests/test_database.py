from datetime import datetime
import sqlite3

from database import FocusDatabase


def test_initialize_applies_schema_migrations(tmp_path) -> None:
    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()

    with sqlite3.connect(database.db_path) as connection:
        applied_versions = [
            int(row[0])
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version ASC").fetchall()
        ]
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(activity_log)").fetchall()
        }

    assert applied_versions == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert "context_tags" in columns
    assert "site_hint" in columns
    assert "classification_confidence" in columns
    assert "classification_source" in columns
    assert "classification_provisional" in columns
    assert "classification_reason" in columns
    assert "classification_fingerprint" in columns


def test_goal_and_intervention_tables_persist(tmp_path) -> None:
    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()

    goal_id = database.upsert_goal(
        goal_type="daily_productive_minutes",
        name="Daily productive minutes",
        target_value=120,
        window_minutes=0,
        schedule_start="08:00",
        schedule_end="18:00",
        days_of_week=[0, 1, 2, 3, 4],
        config={},
    )
    database.record_goal_evaluation(
        goal_id=goal_id,
        timestamp="2026-06-09T10:00:00",
        status="in_progress",
        progress_value=45,
        target_value=120,
        detail="Productive minutes today: 45/120.",
        at_risk=False,
    )
    intervention_id = database.insert_intervention(
        timestamp="2026-06-09T10:05:00",
        action="warn_drift",
        message="Return to your task.",
        reason="Recent activity is distraction-heavy.",
        session_state="drifting",
        goal_id=goal_id,
    )
    database.record_intervention_outcome(
        intervention_id=intervention_id,
        timestamp="2026-06-09T10:20:00",
        outcome_status="success",
        productive_recovered=True,
        distraction_ratio_before=0.8,
        distraction_ratio_after=0.2,
        switch_count_before=12,
        switch_count_after=3,
        notes="Recovered to productive work.",
    )

    goals = database.list_goals()
    evaluations = database.latest_goal_evaluations_for_period(
        datetime.fromisoformat("2026-06-09T00:00:00"),
        datetime.fromisoformat("2026-06-10T00:00:00"),
    )
    interventions = database.list_interventions_for_period(
        datetime.fromisoformat("2026-06-09T00:00:00"),
        datetime.fromisoformat("2026-06-10T00:00:00"),
    )
    stats = database.get_intervention_effectiveness_stats()

    assert goals[0]["name"] == "Daily productive minutes"
    assert evaluations[0]["progress_value"] == 45
    assert interventions[0]["outcome_status"] == "success"
    assert stats["success"] == 1


def test_purge_activity_before_deletes_only_older_rows(tmp_path) -> None:
    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()

    database.insert_activity(
        timestamp="2026-06-01T09:00:00",
        app_name="Code.exe",
        window_title="Editor",
        category="productive",
        duration_seconds=300,
    )
    database.insert_activity(
        timestamp="2026-06-09T09:00:00",
        app_name="Code.exe",
        window_title="Editor",
        category="productive",
        duration_seconds=300,
    )

    deleted_rows = database.purge_activity_before(datetime.fromisoformat("2026-06-05T00:00:00"))
    remaining_rows = database.query_activity_between(
        datetime.fromisoformat("2026-06-01T00:00:00"),
        datetime.fromisoformat("2026-06-10T00:00:00"),
    )

    assert deleted_rows == 1
    assert len(remaining_rows) == 1
    assert remaining_rows[0]["timestamp"] == "2026-06-09T09:00:00"


def test_classification_memory_and_overrides_persist(tmp_path) -> None:
    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()

    database.upsert_classification_memory(
        scope="app",
        key="code.exe",
        app_name="code.exe",
        site_hint="",
        normalized_title="editor",
        category="productive",
        context_tags=["work", "coding"],
        confidence=0.91,
        source="llm",
        provisional=False,
        reason="Looks like a code editor.",
    )
    database.upsert_classification_override(
        scope="site",
        key="youtube.com",
        category="distracting",
        context_tags=["video"],
        reason="Manual correction",
    )

    memory_row = database.get_classification_memory("app", "code.exe")
    override_row = database.get_classification_override("site", "youtube.com")

    assert memory_row is not None
    assert memory_row["category"] == "productive"
    assert override_row is not None
    assert override_row["category"] == "distracting"
