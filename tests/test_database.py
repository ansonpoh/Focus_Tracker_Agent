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

    assert applied_versions == [1, 2, 3]
    assert "context_tags" in columns
    assert "site_hint" in columns


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
