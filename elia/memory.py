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


@dataclass(slots=True)
class GoalRecord:
    id: int
    created_at: str
    updated_at: str
    title: str
    description: str
    priority: float
    status: str
    source: str
    parent_id: int | None


class MemoryStore:
    GOAL_STATUSES = {"active", "blocked", "completed", "abandoned"}

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
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

                CREATE TABLE IF NOT EXISTS goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    priority REAL NOT NULL DEFAULT 0.5,
                    status TEXT NOT NULL DEFAULT 'active',
                    source TEXT NOT NULL DEFAULT 'brain',
                    parent_id INTEGER NULL,
                    FOREIGN KEY(parent_id) REFERENCES goals(id)
                );
                CREATE INDEX IF NOT EXISTS idx_goals_status_priority
                    ON goals(status, priority DESC, id ASC);

                CREATE TABLE IF NOT EXISTS goal_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    goal_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_goal_events_goal
                    ON goal_events(goal_id, id ASC);

                CREATE TABLE IF NOT EXISTS capability_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    executed INTEGER NOT NULL DEFAULT 1,
                    duration_ms REAL NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_capability_events_name_id
                    ON capability_events(capability, id DESC);
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

    def count(self) -> int:
        """Return memory cardinality without materializing autobiographical records."""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM memories").fetchone()
        return int(row["count"] if row else 0)

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

    def create_goal(
        self,
        title: str,
        description: str = "",
        *,
        priority: float = 0.5,
        source: str = "brain",
        parent_id: int | None = None,
    ) -> int:
        title = title.strip()[:240]
        if not title:
            raise ValueError("goal title is required")
        description = description.strip()[:8000]
        priority = max(0.0, min(1.0, float(priority)))
        timestamp = self.now()
        with self._connect() as conn:
            if parent_id is not None:
                parent = conn.execute("SELECT id FROM goals WHERE id=?", (int(parent_id),)).fetchone()
                if parent is None:
                    raise ValueError(f"parent goal does not exist: {parent_id}")
            cur = conn.execute(
                """
                INSERT INTO goals(
                    created_at, updated_at, title, description, priority, status, source, parent_id
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (timestamp, timestamp, title, description, priority, source[:64], parent_id),
            )
            goal_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO goal_events(goal_id, timestamp, kind, content) VALUES (?, ?, 'created', ?)",
                (goal_id, timestamp, description[:4000]),
            )
            return goal_id

    def active_goals(self, limit: int = 16) -> list[GoalRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, updated_at, title, description, priority, status, source, parent_id
                FROM goals
                WHERE status IN ('active', 'blocked')
                ORDER BY priority DESC, id ASC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [self._goal_from_row(row) for row in rows]

    def goal(self, goal_id: int) -> GoalRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, created_at, updated_at, title, description, priority, status, source, parent_id
                FROM goals WHERE id=?
                """,
                (int(goal_id),),
            ).fetchone()
        return self._goal_from_row(row) if row else None

    @staticmethod
    def _goal_from_row(row: sqlite3.Row) -> GoalRecord:
        return GoalRecord(
            id=int(row["id"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            title=str(row["title"]),
            description=str(row["description"]),
            priority=float(row["priority"]),
            status=str(row["status"]),
            source=str(row["source"]),
            parent_id=int(row["parent_id"]) if row["parent_id"] is not None else None,
        )

    def update_goal(
        self,
        goal_id: int,
        *,
        status: str | None = None,
        priority: float | None = None,
        description: str | None = None,
        event: str = "updated",
        evidence: str = "",
    ) -> GoalRecord:
        current = self.goal(goal_id)
        if current is None:
            raise ValueError(f"goal does not exist: {goal_id}")
        next_status = current.status if status is None else str(status).strip().lower()
        if next_status not in self.GOAL_STATUSES:
            raise ValueError(f"invalid goal status: {next_status}")
        next_priority = current.priority if priority is None else max(0.0, min(1.0, float(priority)))
        next_description = current.description if description is None else str(description).strip()[:8000]
        timestamp = self.now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE goals
                SET updated_at=?, description=?, priority=?, status=?
                WHERE id=?
                """,
                (timestamp, next_description, next_priority, next_status, int(goal_id)),
            )
            conn.execute(
                "INSERT INTO goal_events(goal_id, timestamp, kind, content) VALUES (?, ?, ?, ?)",
                (int(goal_id), timestamp, str(event)[:64], str(evidence)[:8000]),
            )
        updated = self.goal(goal_id)
        if updated is None:
            raise RuntimeError("goal disappeared after update")
        return updated

    def goal_events(self, goal_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, kind, content
                FROM goal_events
                WHERE goal_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(goal_id), max(1, int(limit))),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "timestamp": str(row["timestamp"]),
                "kind": str(row["kind"]),
                "content": str(row["content"]),
            }
            for row in reversed(rows)
        ]

    def record_capability_event(
        self,
        capability: str,
        *,
        ok: bool,
        duration_ms: float = 0.0,
        error: str = "",
        executed: bool = True,
    ) -> int:
        name = str(capability).strip()[:128]
        if not name:
            raise ValueError("capability name is required")
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO capability_events(timestamp, capability, ok, executed, duration_ms, error)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self.now(),
                    name,
                    1 if ok else 0,
                    1 if executed else 0,
                    max(0.0, float(duration_ms)),
                    str(error)[:4000],
                ),
            )
            return int(cur.lastrowid)

    def capability_health(self, capability: str, window: int = 20) -> dict[str, Any]:
        name = str(capability).strip()
        limit = max(1, min(int(window), 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, ok, executed, duration_ms, error
                FROM capability_events
                WHERE capability=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (name, limit),
            ).fetchall()

        executed_rows = [row for row in rows if bool(row["executed"])]
        successes = sum(1 for row in executed_rows if bool(row["ok"]))
        failures = sum(1 for row in executed_rows if not bool(row["ok"]))
        suppressed = sum(1 for row in rows if not bool(row["executed"]))
        consecutive_failures = 0
        for row in executed_rows:
            if bool(row["ok"]):
                break
            consecutive_failures += 1
        last_error = ""
        for row in rows:
            if str(row["error"]):
                last_error = str(row["error"])
                break
        average_duration_ms = (
            sum(float(row["duration_ms"]) for row in executed_rows) / len(executed_rows)
            if executed_rows
            else 0.0
        )
        return {
            "capability": name,
            "window": limit,
            "events": len(rows),
            "attempts": len(executed_rows),
            "successes": successes,
            "failures": failures,
            "suppressed": suppressed,
            "success_rate": successes / len(executed_rows) if executed_rows else None,
            "consecutive_failures": consecutive_failures,
            "average_duration_ms": average_duration_ms,
            "last_error": last_error or None,
            "last_event_at": str(rows[0]["timestamp"]) if rows else None,
        }

    def capability_health_all(
        self, capabilities: list[str] | None = None, window: int = 20
    ) -> dict[str, dict[str, Any]]:
        if capabilities is None:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT capability FROM capability_events ORDER BY capability ASC"
                ).fetchall()
            capabilities = [str(row["capability"]) for row in rows]
        return {name: self.capability_health(name, window=window) for name in capabilities}

    def capability_degraded(self, capability: str, threshold: int = 3) -> bool:
        health = self.capability_health(capability)
        return int(health["consecutive_failures"]) >= max(1, int(threshold))

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
