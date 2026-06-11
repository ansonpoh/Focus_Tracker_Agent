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
