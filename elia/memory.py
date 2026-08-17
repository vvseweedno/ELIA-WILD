from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from typing import Any


@dataclass(slots=True)
class MemoryRecord:
    id: int
    timestamp: str
    kind: str
    content: str
    importance: float
    source: str
    metadata: dict[str, Any]


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance REAL NOT NULL DEFAULT 0.5,
                    source TEXT NOT NULL DEFAULT 'runtime',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_memories_time
                    ON memories(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_memories_importance
                    ON memories(importance DESC);

                CREATE TABLE IF NOT EXISTS metrics (
                    key TEXT PRIMARY KEY,
                    value REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def remember(
        self,
        kind: str,
        content: str,
        *,
        importance: float = 0.5,
        source: str = "runtime",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        importance = max(0.0, min(1.0, float(importance)))
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO memories(timestamp, kind, content, importance, source, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self.now(),
                    kind,
                    content,
                    importance,
                    source,
                    json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
            return int(cur.lastrowid)

    def recent(self, limit: int = 12) -> list[MemoryRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, kind, content, importance, source, metadata_json
                FROM memories
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [
            MemoryRecord(
                id=int(row["id"]),
                timestamp=str(row["timestamp"]),
                kind=str(row["kind"]),
                content=str(row["content"]),
                importance=float(row["importance"]),
                source=str(row["source"]),
                metadata=json.loads(row["metadata_json"]),
            )
            for row in reversed(rows)
        ]

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    @staticmethod
    def _week_suffix(moment: datetime | None = None) -> str:
        moment = moment or datetime.now(timezone.utc)
        iso = moment.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    def _metric_key(self, metric: str) -> str:
        return f"{metric}:{self._week_suffix()}"

    def add_metric(self, metric: str, amount: float) -> float:
        key = self._metric_key(metric)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO metrics(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=value+excluded.value",
                (key, max(0.0, float(amount))),
            )
            row = conn.execute("SELECT value FROM metrics WHERE key=?", (key,)).fetchone()
        return float(row["value"])

    def metric_this_week(self, metric: str) -> float:
        key = self._metric_key(metric)
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM metrics WHERE key=?", (key,)).fetchone()
        return float(row["value"]) if row else 0.0

    def add_brain_seconds(self, seconds: float) -> float:
        return self.add_metric("brain_seconds", seconds)

    def brain_seconds_this_week(self) -> float:
        return self.metric_this_week("brain_seconds")

    def add_runtime_seconds(self, seconds: float) -> float:
        return self.add_metric("gpu_runtime_seconds", seconds)

    def runtime_seconds_this_week(self) -> float:
        return self.metric_this_week("gpu_runtime_seconds")
