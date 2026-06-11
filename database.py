from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
import json
import sqlite3


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


SCHEMA_MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            app_name TEXT NOT NULL,
            window_title TEXT,
            category TEXT NOT NULL,
            duration_seconds INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS nudges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            nudge_type TEXT NOT NULL,
            message TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS daily_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            productive_seconds INTEGER,
            distracting_seconds INTEGER,
            neutral_seconds INTEGER,
            unknown_seconds INTEGER,
            summary_text TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_activity_log_timestamp
        ON activity_log (timestamp);

        CREATE INDEX IF NOT EXISTS idx_activity_log_timestamp_category
        ON activity_log (timestamp, category);

        CREATE INDEX IF NOT EXISTS idx_activity_log_timestamp_app_name
        ON activity_log (timestamp, app_name);
        """,
    ),
    (
        2,
        """
        ALTER TABLE activity_log ADD COLUMN context_tags TEXT NOT NULL DEFAULT '[]';
        """,
    ),
    (
        3,
        """
        ALTER TABLE activity_log ADD COLUMN site_hint TEXT NOT NULL DEFAULT '';
        """,
    ),
    (
        4,
        """
        ALTER TABLE activity_log ADD COLUMN classification_confidence REAL NOT NULL DEFAULT 0;
        ALTER TABLE activity_log ADD COLUMN classification_source TEXT NOT NULL DEFAULT 'legacy';
        ALTER TABLE activity_log ADD COLUMN classification_provisional INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE activity_log ADD COLUMN classification_reason TEXT NOT NULL DEFAULT '';
        ALTER TABLE activity_log ADD COLUMN classification_fingerprint TEXT NOT NULL DEFAULT '';
        """,
    ),
    (
        5,
        """
        CREATE TABLE IF NOT EXISTS classification_memory (
            match_scope TEXT NOT NULL,
            match_key TEXT NOT NULL,
            app_name TEXT NOT NULL,
            site_hint TEXT NOT NULL,
            normalized_title TEXT NOT NULL,
            category TEXT NOT NULL,
            context_tags TEXT NOT NULL,
            confidence REAL NOT NULL,
            source TEXT NOT NULL,
            provisional INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            hit_count INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            PRIMARY KEY (match_scope, match_key)
        );
        """,
    ),
    (
        6,
        """
        CREATE TABLE IF NOT EXISTS classification_overrides (
            match_scope TEXT NOT NULL,
            match_key TEXT NOT NULL,
            category TEXT NOT NULL,
            context_tags TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'override',
            provisional INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (match_scope, match_key)
        );
        """,
    ),
    (
        7,
        """
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_type TEXT NOT NULL,
            name TEXT NOT NULL,
            target_value INTEGER NOT NULL,
            window_minutes INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            schedule_start TEXT NOT NULL DEFAULT '00:00',
            schedule_end TEXT NOT NULL DEFAULT '23:59',
            days_of_week TEXT NOT NULL DEFAULT '[0,1,2,3,4,5,6]',
            config_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        8,
        """
        CREATE TABLE IF NOT EXISTS goal_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            status TEXT NOT NULL,
            progress_value INTEGER NOT NULL,
            target_value INTEGER NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            at_risk INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (goal_id) REFERENCES goals (id)
        );
        CREATE INDEX IF NOT EXISTS idx_goal_evaluations_goal_timestamp
        ON goal_evaluations (goal_id, timestamp);
        """,
    ),
    (
        9,
        """
        CREATE TABLE IF NOT EXISTS interventions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action TEXT NOT NULL,
            message TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            session_state TEXT NOT NULL DEFAULT '',
            goal_id INTEGER,
            FOREIGN KEY (goal_id) REFERENCES goals (id)
        );
        CREATE INDEX IF NOT EXISTS idx_interventions_action_timestamp
        ON interventions (action, timestamp);

        CREATE TABLE IF NOT EXISTS intervention_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            intervention_id INTEGER NOT NULL UNIQUE,
            timestamp TEXT NOT NULL,
            outcome_status TEXT NOT NULL,
            productive_recovered INTEGER NOT NULL DEFAULT 0,
            distraction_ratio_before REAL NOT NULL DEFAULT 0,
            distraction_ratio_after REAL NOT NULL DEFAULT 0,
            switch_count_before INTEGER NOT NULL DEFAULT 0,
            switch_count_after INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (intervention_id) REFERENCES interventions (id)
        );
        """,
    ),
    (
        10,
        """
        CREATE TABLE IF NOT EXISTS session_state_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            session_state TEXT NOT NULL,
            productive_streak_seconds INTEGER NOT NULL DEFAULT 0,
            switch_count INTEGER NOT NULL DEFAULT 0,
            distraction_ratio REAL NOT NULL DEFAULT 0,
            productive_ratio REAL NOT NULL DEFAULT 0,
            detail TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_session_state_timestamp
        ON session_state_snapshots (timestamp);
        """,
    ),
]


def count_switches(rows: Iterable[dict[str, Any]], cutoff: datetime | None = None) -> int:
    previous_key: tuple[str, str] | None = None
    switches = 0

    for row in rows:
        current_key = (
            str(row.get("app_name") or ""),
            str(row.get("window_title") or ""),
        )

        if previous_key is not None and current_key != previous_key:
            if cutoff is None:
                switches += 1
            else:
                row_timestamp = row.get("timestamp")
                if isinstance(row_timestamp, str) and _parse_iso(row_timestamp) >= cutoff:
                    switches += 1

        previous_key = current_key

    return switches


def productive_streak_seconds(
    rows: list[dict[str, Any]],
    *,
    max_gap_seconds: int = 15,
) -> int:
    if not rows:
        return 0

    streak_seconds = 0
    previous_timestamp: datetime | None = None

    for row in reversed(rows):
        if str(row.get("category") or "").lower() != "productive":
            break

        timestamp_value = row.get("timestamp")
        if not isinstance(timestamp_value, str):
            break

        current_timestamp = _parse_iso(timestamp_value)
        if previous_timestamp is not None:
            gap = (previous_timestamp - current_timestamp).total_seconds()
            if gap > max_gap_seconds:
                break

        streak_seconds += int(row.get("duration_seconds") or 0)
        previous_timestamp = current_timestamp

    return streak_seconds


@dataclass
class FocusDatabase:
    db_path: Path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL;")
            self._ensure_migrations_table(connection)
            self._apply_migrations(connection)
            connection.execute(
                """
                DELETE FROM daily_summary
                WHERE id NOT IN (
                    SELECT MAX(id)
                    FROM daily_summary
                    GROUP BY date
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_summary_date_unique
                ON daily_summary (date)
                """
            )

    def _ensure_migrations_table(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )

    def _apply_migrations(self, connection: sqlite3.Connection) -> None:
        applied_versions = {
            int(row["version"])
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        for version, sql in SCHEMA_MIGRATIONS:
            if version in applied_versions:
                continue
            try:
                connection.executescript(sql)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, _iso(datetime.now())),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def insert_activity(
        self,
        *,
        timestamp: str,
        app_name: str,
        window_title: str,
        category: str,
        duration_seconds: int,
        context_tags: list[str] | None = None,
        site_hint: str = "",
        classification_confidence: float = 0.0,
        classification_source: str = "legacy",
        classification_provisional: bool = False,
        classification_reason: str = "",
        classification_fingerprint: str = "",
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO activity_log (
                    timestamp, app_name, window_title, category, duration_seconds, context_tags, site_hint,
                    classification_confidence, classification_source, classification_provisional,
                    classification_reason, classification_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    app_name or "unknown",
                    window_title or "",
                    category or "unknown",
                    int(duration_seconds),
                    json.dumps(context_tags or []),
                    site_hint or "",
                    float(classification_confidence),
                    classification_source or "legacy",
                    1 if classification_provisional else 0,
                    classification_reason or "",
                    classification_fingerprint or "",
                ),
            )

    def insert_nudge(self, *, timestamp: str, nudge_type: str, message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO nudges (timestamp, nudge_type, message)
                VALUES (?, ?, ?)
                """,
                (timestamp, nudge_type, message),
            )

    def upsert_daily_summary(
        self,
        *,
        date: str,
        productive_seconds: int,
        distracting_seconds: int,
        neutral_seconds: int,
        unknown_seconds: int,
        summary_text: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO daily_summary (
                    date, productive_seconds, distracting_seconds,
                    neutral_seconds, unknown_seconds, summary_text
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    productive_seconds = excluded.productive_seconds,
                    distracting_seconds = excluded.distracting_seconds,
                    neutral_seconds = excluded.neutral_seconds,
                    unknown_seconds = excluded.unknown_seconds,
                    summary_text = excluded.summary_text
                """,
                (
                    date,
                    int(productive_seconds),
                    int(distracting_seconds),
                    int(neutral_seconds),
                    int(unknown_seconds),
                    summary_text,
                ),
            )

    def query_activity_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    timestamp,
                    app_name,
                    window_title,
                    category,
                    duration_seconds,
                    context_tags,
                    site_hint,
                    classification_confidence,
                    classification_source,
                    classification_provisional,
                    classification_reason,
                    classification_fingerprint
                FROM activity_log
                WHERE timestamp >= ? AND timestamp < ?
                ORDER BY timestamp ASC, id ASC
                """,
                (_iso(start), _iso(end)),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def query_activity_for_date(self, date: str) -> list[dict[str, Any]]:
        start = datetime.fromisoformat(f"{date}T00:00:00")
        end = start + timedelta(days=1)
        return self.query_activity_between(start, end)

    def get_daily_totals(self, date: str) -> dict[str, int]:
        start = datetime.fromisoformat(f"{date}T00:00:00")
        end = start + timedelta(days=1)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COALESCE(SUM(duration_seconds), 0) AS total_seconds,
                    COALESCE(SUM(CASE WHEN category = 'productive' THEN duration_seconds ELSE 0 END), 0) AS productive_seconds,
                    COALESCE(SUM(CASE WHEN category = 'distracting' THEN duration_seconds ELSE 0 END), 0) AS distracting_seconds,
                    COALESCE(SUM(CASE WHEN category = 'neutral' THEN duration_seconds ELSE 0 END), 0) AS neutral_seconds,
                    COALESCE(SUM(CASE WHEN category = 'unknown' THEN duration_seconds ELSE 0 END), 0) AS unknown_seconds
                FROM activity_log
                WHERE timestamp >= ? AND timestamp < ?
                """,
                (_iso(start), _iso(end)),
            ).fetchone()
        return {
            "total_seconds": int(row["total_seconds"] or 0),
            "productive_seconds": int(row["productive_seconds"] or 0),
            "distracting_seconds": int(row["distracting_seconds"] or 0),
            "neutral_seconds": int(row["neutral_seconds"] or 0),
            "unknown_seconds": int(row["unknown_seconds"] or 0),
        }

    def get_top_apps(self, date: str, limit: int = 5) -> list[dict[str, Any]]:
        start = datetime.fromisoformat(f"{date}T00:00:00")
        end = start + timedelta(days=1)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT app_name, SUM(duration_seconds) AS total_seconds
                FROM activity_log
                WHERE timestamp >= ? AND timestamp < ?
                GROUP BY app_name
                ORDER BY total_seconds DESC, app_name ASC
                LIMIT ?
                """,
                (_iso(start), _iso(end), limit),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_top_distracting_titles(self, date: str, limit: int = 5) -> list[dict[str, Any]]:
        start = datetime.fromisoformat(f"{date}T00:00:00")
        end = start + timedelta(days=1)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    COALESCE(NULLIF(TRIM(window_title), ''), app_name) AS title,
                    SUM(duration_seconds) AS total_seconds
                FROM activity_log
                WHERE timestamp >= ? AND timestamp < ?
                  AND category = 'distracting'
                GROUP BY COALESCE(NULLIF(TRIM(window_title), ''), app_name)
                ORDER BY total_seconds DESC, title ASC
                LIMIT ?
                """,
                (_iso(start), _iso(end), limit),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_recent_nudge_timestamp(self, nudge_type: str) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT timestamp
                FROM nudges
                WHERE nudge_type = ?
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """,
                (nudge_type,),
            ).fetchone()

        if row is None:
            return None

        timestamp_value = row["timestamp"]
        if not isinstance(timestamp_value, str):
            return None
        return _parse_iso(timestamp_value)

    def get_recent_activity(
        self,
        *,
        minutes: int,
        now: datetime | None = None,
        extra_buffer_minutes: int = 0,
    ) -> list[dict[str, Any]]:
        current_time = now or datetime.now()
        start = current_time - timedelta(minutes=minutes + extra_buffer_minutes)
        end = current_time + timedelta(seconds=1)
        return self.query_activity_between(start, end)

    def get_recent_switch_count(
        self,
        *,
        minutes: int,
        now: datetime | None = None,
    ) -> int:
        current_time = now or datetime.now()
        rows = self.get_recent_activity(minutes=minutes, now=current_time, extra_buffer_minutes=1)
        cutoff = current_time - timedelta(minutes=minutes)
        return count_switches(rows, cutoff=cutoff)

    def get_current_productive_streak_seconds(
        self,
        *,
        max_minutes: int = 90,
        now: datetime | None = None,
    ) -> int:
        current_time = now or datetime.now()
        rows = self.get_recent_activity(minutes=max_minutes, now=current_time)
        return productive_streak_seconds(rows)

    def purge_activity_before(self, cutoff: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM activity_log
                WHERE timestamp < ?
                """,
                (_iso(cutoff),),
            )
            return int(cursor.rowcount or 0)

    def get_classification_memory(self, scope: str, key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT match_scope, match_key, app_name, site_hint, normalized_title, category,
                       context_tags, confidence, source, provisional, reason, hit_count, first_seen, last_seen
                FROM classification_memory
                WHERE match_scope = ? AND match_key = ?
                """,
                (scope, key),
            ).fetchone()
        return _row_to_dict(row) if row is not None else None

    def upsert_classification_memory(
        self,
        *,
        scope: str,
        key: str,
        app_name: str,
        site_hint: str,
        normalized_title: str,
        category: str,
        context_tags: list[str],
        confidence: float,
        source: str,
        provisional: bool,
        reason: str,
    ) -> None:
        timestamp = _iso(datetime.now())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO classification_memory (
                    match_scope, match_key, app_name, site_hint, normalized_title, category,
                    context_tags, confidence, source, provisional, reason, hit_count, first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(match_scope, match_key) DO UPDATE SET
                    app_name = excluded.app_name,
                    site_hint = excluded.site_hint,
                    normalized_title = excluded.normalized_title,
                    category = excluded.category,
                    context_tags = excluded.context_tags,
                    confidence = excluded.confidence,
                    source = excluded.source,
                    provisional = excluded.provisional,
                    reason = excluded.reason,
                    hit_count = classification_memory.hit_count + 1,
                    last_seen = excluded.last_seen
                """,
                (
                    scope,
                    key,
                    app_name,
                    site_hint,
                    normalized_title,
                    category,
                    json.dumps(context_tags or []),
                    float(confidence),
                    source,
                    1 if provisional else 0,
                    reason or "",
                    timestamp,
                    timestamp,
                ),
            )

    def get_classification_override(self, scope: str, key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT match_scope, match_key, category, context_tags, confidence, source, provisional, reason, updated_at
                FROM classification_overrides
                WHERE match_scope = ? AND match_key = ?
                """,
                (scope, key),
            ).fetchone()
        return _row_to_dict(row) if row is not None else None

    def upsert_classification_override(
        self,
        *,
        scope: str,
        key: str,
        category: str,
        context_tags: list[str],
        reason: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO classification_overrides (
                    match_scope, match_key, category, context_tags, confidence, source, provisional, reason, updated_at
                ) VALUES (?, ?, ?, ?, 1, 'override', 0, ?, ?)
                ON CONFLICT(match_scope, match_key) DO UPDATE SET
                    category = excluded.category,
                    context_tags = excluded.context_tags,
                    confidence = excluded.confidence,
                    source = excluded.source,
                    provisional = excluded.provisional,
                    reason = excluded.reason,
                    updated_at = excluded.updated_at
                """,
                (
                    scope,
                    key,
                    category,
                    json.dumps(context_tags or []),
                    reason or "",
                    _iso(datetime.now()),
                ),
            )

    def list_classification_review_candidates(
        self,
        *,
        limit: int = 20,
        confidence_threshold: float = 0.75,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT match_scope, match_key, app_name, site_hint, normalized_title, category,
                       context_tags, confidence, source, provisional, reason, hit_count, first_seen, last_seen
                FROM classification_memory
                WHERE provisional = 1 OR confidence < ?
                ORDER BY provisional DESC, confidence ASC, last_seen DESC
                LIMIT ?
                """,
                (float(confidence_threshold), int(limit)),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def upsert_goal(
        self,
        *,
        goal_type: str,
        name: str,
        target_value: int,
        window_minutes: int,
        schedule_start: str,
        schedule_end: str,
        days_of_week: list[int],
        config: dict[str, Any] | None = None,
        active: bool = True,
    ) -> int:
        timestamp = _iso(datetime.now())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO goals (
                    goal_type, name, target_value, window_minutes, active,
                    schedule_start, schedule_end, days_of_week, config_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    goal_type,
                    name,
                    int(target_value),
                    int(window_minutes),
                    1 if active else 0,
                    schedule_start,
                    schedule_end,
                    json.dumps(days_of_week),
                    json.dumps(config or {}),
                    timestamp,
                    timestamp,
                ),
            )
            return int(cursor.lastrowid)

    def disable_goal(self, goal_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE goals
                SET active = 0, updated_at = ?
                WHERE id = ?
                """,
                (_iso(datetime.now()), int(goal_id)),
            )

    def list_goals(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        query = """
            SELECT id, goal_type, name, target_value, window_minutes, active,
                   schedule_start, schedule_end, days_of_week, config_json, created_at, updated_at
            FROM goals
        """
        params: tuple[Any, ...] = ()
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY active DESC, id ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def record_goal_evaluation(
        self,
        *,
        goal_id: int,
        timestamp: str,
        status: str,
        progress_value: int,
        target_value: int,
        detail: str,
        at_risk: bool,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO goal_evaluations (
                    goal_id, timestamp, status, progress_value, target_value, detail, at_risk
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(goal_id),
                    timestamp,
                    status,
                    int(progress_value),
                    int(target_value),
                    detail,
                    1 if at_risk else 0,
                ),
            )

    def latest_goal_evaluations_for_period(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT ge.goal_id, g.name, g.goal_type, ge.timestamp, ge.status, ge.progress_value, ge.target_value, ge.detail, ge.at_risk
                FROM goal_evaluations ge
                JOIN goals g ON g.id = ge.goal_id
                JOIN (
                    SELECT goal_id, MAX(timestamp) AS max_timestamp
                    FROM goal_evaluations
                    WHERE timestamp >= ? AND timestamp < ?
                    GROUP BY goal_id
                ) latest
                  ON latest.goal_id = ge.goal_id
                 AND latest.max_timestamp = ge.timestamp
                ORDER BY ge.goal_id ASC
                """,
                (_iso(start), _iso(end)),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def insert_intervention(
        self,
        *,
        timestamp: str,
        action: str,
        message: str,
        reason: str,
        session_state: str,
        goal_id: int | None,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO interventions (timestamp, action, message, reason, session_state, goal_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (timestamp, action, message, reason, session_state, goal_id),
            )
            return int(cursor.lastrowid)

    def list_pending_interventions(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT i.id, i.timestamp, i.action, i.message, i.reason, i.session_state, i.goal_id
                FROM interventions i
                LEFT JOIN intervention_outcomes io ON io.intervention_id = i.id
                WHERE io.intervention_id IS NULL
                ORDER BY i.timestamp ASC, i.id ASC
                """
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def record_intervention_outcome(
        self,
        *,
        intervention_id: int,
        timestamp: str,
        outcome_status: str,
        productive_recovered: bool,
        distraction_ratio_before: float,
        distraction_ratio_after: float,
        switch_count_before: int,
        switch_count_after: int,
        notes: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO intervention_outcomes (
                    intervention_id, timestamp, outcome_status, productive_recovered,
                    distraction_ratio_before, distraction_ratio_after, switch_count_before, switch_count_after, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(intervention_id) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    outcome_status = excluded.outcome_status,
                    productive_recovered = excluded.productive_recovered,
                    distraction_ratio_before = excluded.distraction_ratio_before,
                    distraction_ratio_after = excluded.distraction_ratio_after,
                    switch_count_before = excluded.switch_count_before,
                    switch_count_after = excluded.switch_count_after,
                    notes = excluded.notes
                """,
                (
                    int(intervention_id),
                    timestamp,
                    outcome_status,
                    1 if productive_recovered else 0,
                    float(distraction_ratio_before),
                    float(distraction_ratio_after),
                    int(switch_count_before),
                    int(switch_count_after),
                    notes,
                ),
            )

    def get_recent_intervention_timestamp(self, action: str) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT timestamp
                FROM interventions
                WHERE action = ?
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """,
                (action,),
            ).fetchone()
        if row is None or not isinstance(row["timestamp"], str):
            return None
        return _parse_iso(row["timestamp"])

    def get_intervention_effectiveness_stats(self, *, action: str | None = None, start: datetime | None = None, end: datetime | None = None) -> dict[str, int]:
        query = """
            SELECT io.outcome_status, COUNT(*) AS count_value
            FROM intervention_outcomes io
            JOIN interventions i ON i.id = io.intervention_id
            WHERE 1 = 1
        """
        params: list[Any] = []
        if action is not None:
            query += " AND i.action = ?"
            params.append(action)
        if start is not None:
            query += " AND i.timestamp >= ?"
            params.append(_iso(start))
        if end is not None:
            query += " AND i.timestamp < ?"
            params.append(_iso(end))
        query += " GROUP BY io.outcome_status"
        stats = {"success": 0, "partial": 0, "failure": 0, "pending": 0, "total": 0}
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        for row in rows:
            status = str(row["outcome_status"] or "")
            count_value = int(row["count_value"] or 0)
            if status in stats:
                stats[status] = count_value
            stats["total"] += count_value
        return stats

    def list_interventions_for_period(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT i.id, i.timestamp, i.action, i.message, i.reason, i.session_state, i.goal_id,
                       io.outcome_status, io.productive_recovered, io.distraction_ratio_before,
                       io.distraction_ratio_after, io.switch_count_before, io.switch_count_after, io.notes
                FROM interventions i
                LEFT JOIN intervention_outcomes io ON io.intervention_id = i.id
                WHERE i.timestamp >= ? AND i.timestamp < ?
                ORDER BY i.timestamp ASC, i.id ASC
                """,
                (_iso(start), _iso(end)),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def insert_session_state_snapshot(
        self,
        *,
        timestamp: str,
        session_state: str,
        productive_streak_seconds: int,
        switch_count: int,
        distraction_ratio: float,
        productive_ratio: float,
        detail: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO session_state_snapshots (
                    timestamp, session_state, productive_streak_seconds, switch_count,
                    distraction_ratio, productive_ratio, detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    session_state,
                    int(productive_streak_seconds),
                    int(switch_count),
                    float(distraction_ratio),
                    float(productive_ratio),
                    detail,
                ),
            )

    def list_session_states_for_period(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT timestamp, session_state, productive_streak_seconds, switch_count,
                       distraction_ratio, productive_ratio, detail
                FROM session_state_snapshots
                WHERE timestamp >= ? AND timestamp < ?
                ORDER BY timestamp ASC, id ASC
                """,
                (_iso(start), _iso(end)),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def count_focus_blocks_for_date(self, *, goal_date: str, minimum_duration_seconds: int) -> int:
        rows = self.query_activity_for_date(goal_date)
        current_block = 0
        count_value = 0
        previous_timestamp: datetime | None = None
        for row in rows:
            timestamp_value = row.get("timestamp")
            category = str(row.get("category") or "").lower()
            if not isinstance(timestamp_value, str) or category != "productive":
                if current_block >= minimum_duration_seconds:
                    count_value += 1
                current_block = 0
                previous_timestamp = None
                continue
            current_timestamp = _parse_iso(timestamp_value)
            if previous_timestamp is not None and (current_timestamp - previous_timestamp).total_seconds() > 15:
                if current_block >= minimum_duration_seconds:
                    count_value += 1
                current_block = 0
            current_block += int(row.get("duration_seconds") or 0)
            previous_timestamp = current_timestamp
        if current_block >= minimum_duration_seconds:
            count_value += 1
        return count_value
