"""SQLite storage layer for activity sessions."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name         TEXT    NOT NULL,
    display_name     TEXT,
    window_title     TEXT,
    process_name     TEXT,
    exe_path         TEXT,
    category         TEXT,
    start_time       REAL    NOT NULL,
    end_time         REAL,
    duration_seconds REAL,
    is_idle          INTEGER NOT NULL DEFAULT 0,
    device           TEXT    NOT NULL DEFAULT 'pc'
);

CREATE INDEX IF NOT EXISTS idx_sessions_start    ON sessions(start_time);
CREATE INDEX IF NOT EXISTS idx_sessions_app      ON sessions(app_name);
CREATE INDEX IF NOT EXISTS idx_sessions_category ON sessions(category);
CREATE INDEX IF NOT EXISTS idx_sessions_idle     ON sessions(is_idle);
CREATE INDEX IF NOT EXISTS idx_sessions_device   ON sessions(device);
"""

_MIGRATION_ADD_DEVICE = """
ALTER TABLE sessions ADD COLUMN device TEXT NOT NULL DEFAULT 'pc'
"""


@dataclass
class Session:
    id: int | None
    app_name: str
    display_name: str
    window_title: str
    process_name: str
    exe_path: str
    category: str
    start_time: float
    end_time: float | None
    duration_seconds: float | None
    is_idle: bool
    device: str = "pc"


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        # Migration: add device column for existing databases
        try:
            self._conn.execute(_MIGRATION_ADD_DEVICE)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_device ON sessions(device)"
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @contextmanager
    def _cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        assert self._conn, "Database not connected — call connect() first"
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    # ── Write operations ──────────────────────────────────────────────────────

    def open_session(
        self,
        app_name: str,
        display_name: str,
        window_title: str,
        process_name: str,
        exe_path: str,
        category: str,
        is_idle: bool,
        start_time: float | None = None,
        device: str = "pc",
    ) -> int:
        """Insert a new open session; return its row ID."""
        now = start_time or time.time()
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO sessions
                    (app_name, display_name, window_title, process_name,
                     exe_path, category, start_time, is_idle, device)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (app_name, display_name, window_title, process_name,
                 exe_path, category, now, int(is_idle), device),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def insert_completed_session(
        self,
        app_name: str,
        display_name: str,
        window_title: str,
        process_name: str,
        exe_path: str,
        category: str,
        is_idle: bool,
        start_time: float,
        end_time: float,
        device: str = "pc",
    ) -> None:
        """Insert a fully completed session from a remote source (e.g. phone sync)."""
        duration = end_time - start_time
        if duration < 1:
            return
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO sessions
                    (app_name, display_name, window_title, process_name,
                     exe_path, category, start_time, end_time, duration_seconds, is_idle, device)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (app_name, display_name, window_title, process_name,
                 exe_path, category, start_time, end_time, duration, int(is_idle), device),
            )

    def close_session(
        self,
        session_id: int,
        end_time: float | None = None,
        min_duration: float = 3.0,
    ) -> float:
        """
        Close an open session and compute its duration.
        Sessions shorter than min_duration are deleted.
        Returns actual duration in seconds.
        """
        now = end_time or time.time()
        with self._cursor() as cur:
            cur.execute("SELECT start_time FROM sessions WHERE id = ?", (session_id,))
            row = cur.fetchone()
            if not row:
                return 0.0
            duration = now - row["start_time"]
            if duration < min_duration:
                cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                return 0.0
            cur.execute(
                """
                UPDATE sessions
                SET end_time = ?, duration_seconds = ?
                WHERE id = ?
                """,
                (now, duration, session_id),
            )
            return duration

    # ── Read operations ───────────────────────────────────────────────────────

    def get_sessions(
        self,
        start_ts: float,
        end_ts: float,
        include_idle: bool = True,
        device: str | None = None,
    ) -> list[Session]:
        """Return all completed sessions in a time range."""
        query = """
            SELECT * FROM sessions
            WHERE start_time >= ? AND start_time < ?
              AND end_time IS NOT NULL
        """
        params: list = [start_ts, end_ts]
        if not include_idle:
            query += " AND is_idle = 0"
        if device:
            query += " AND device = ?"
            params.append(device)
        query += " ORDER BY start_time"

        with self._cursor() as cur:
            cur.execute(query, params)
            return [_row_to_session(r) for r in cur.fetchall()]

    def get_app_totals(
        self,
        start_ts: float,
        end_ts: float,
        include_idle: bool = False,
        device: str | None = None,
    ) -> list[dict]:
        """Aggregate total time per display_name in a range."""
        query = """
            SELECT
                display_name,
                app_name,
                category,
                device,
                SUM(duration_seconds) AS total_seconds,
                COUNT(*) AS session_count
            FROM sessions
            WHERE start_time >= ? AND start_time < ?
              AND end_time IS NOT NULL
              AND is_idle = ?
        """
        params: list = [start_ts, end_ts, 0 if not include_idle else 1]
        if device:
            query += " AND device = ?"
            params.append(device)
        query += " GROUP BY display_name ORDER BY total_seconds DESC"
        with self._cursor() as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def get_category_totals(
        self,
        start_ts: float,
        end_ts: float,
        device: str | None = None,
    ) -> list[dict]:
        """Aggregate total time per category in a range."""
        query = """
            SELECT
                COALESCE(category, 'Other') AS category,
                SUM(duration_seconds) AS total_seconds,
                COUNT(*) AS session_count
            FROM sessions
            WHERE start_time >= ? AND start_time < ?
              AND end_time IS NOT NULL
              AND is_idle = 0
        """
        params: list = [start_ts, end_ts]
        if device:
            query += " AND device = ?"
            params.append(device)
        query += " GROUP BY category ORDER BY total_seconds DESC"
        with self._cursor() as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def get_idle_total(
        self,
        start_ts: float,
        end_ts: float,
        device: str | None = None,
    ) -> float:
        """Return total idle seconds in range."""
        query = """
            SELECT COALESCE(SUM(duration_seconds), 0)
            FROM sessions
            WHERE start_time >= ? AND start_time < ?
              AND end_time IS NOT NULL AND is_idle = 1
        """
        params: list = [start_ts, end_ts]
        if device:
            query += " AND device = ?"
            params.append(device)
        with self._cursor() as cur:
            cur.execute(query, params)
            return float(cur.fetchone()[0])

    def get_hourly_breakdown(
        self,
        start_ts: float,
        end_ts: float,
        device: str | None = None,
    ) -> dict[int, float]:
        """Return active seconds per hour-of-day for a range."""
        sessions = self.get_sessions(start_ts, end_ts, include_idle=False, device=device)
        hourly: dict[int, float] = {h: 0.0 for h in range(24)}
        for s in sessions:
            if s.end_time is None or s.duration_seconds is None:
                continue
            # Distribute session duration across the hours it spans
            t = s.start_time
            end = s.end_time
            while t < end:
                import datetime
                hour = datetime.datetime.fromtimestamp(t).hour
                next_hour_start = (
                    datetime.datetime.fromtimestamp(t)
                    .replace(minute=0, second=0, microsecond=0)
                    .timestamp()
                    + 3600
                )
                chunk = min(next_hour_start, end) - t
                hourly[hour] += chunk
                t = next_hour_start
        return hourly

    def get_recent_activity(self, limit: int = 20, device: str | None = None) -> list[dict]:
        """Return the most recent completed sessions."""
        query = """
            SELECT display_name, window_title, category, start_time,
                   end_time, duration_seconds, is_idle, device
            FROM sessions
            WHERE end_time IS NOT NULL
        """
        params: list = []
        if device:
            query += " AND device = ?"
            params.append(device)
        query += " ORDER BY start_time DESC LIMIT ?"
        params.append(limit)
        with self._cursor() as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def get_available_dates(self, limit: int = 90) -> list[str]:
        """Return ISO date strings (YYYY-MM-DD) that have recorded sessions."""
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT date(start_time, 'unixepoch', 'localtime') AS d
                FROM sessions
                WHERE end_time IS NOT NULL
                ORDER BY d DESC
                LIMIT ?
                """,
                [limit],
            )
            return [r[0] for r in cur.fetchall()]


def _row_to_session(row: sqlite3.Row) -> Session:
    keys = row.keys()
    return Session(
        id=row["id"],
        app_name=row["app_name"],
        display_name=row["display_name"] or row["app_name"],
        window_title=row["window_title"] or "",
        process_name=row["process_name"] or "",
        exe_path=row["exe_path"] or "",
        category=row["category"] or "Other",
        start_time=row["start_time"],
        end_time=row["end_time"],
        duration_seconds=row["duration_seconds"],
        is_idle=bool(row["is_idle"]),
        device=row["device"] if "device" in keys else "pc",
    )
