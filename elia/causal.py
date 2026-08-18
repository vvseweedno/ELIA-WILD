from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class InterventionExperience:
    id: int
    observed_at: str
    transaction_id: str | None
    action_name: str
    arguments_fingerprint: str
    success: bool
    observation_id: int | None
    duration_ms: float
    outcome_fingerprint: str
    outcome_summary: str
    source: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CausalMemoryStore:
    """Experience memory over actual interventions and their observed outcomes.

    An action/outcome pair is evidence about an intervention, not proof of causality.
    The store intentionally reports empirical strategy statistics and never upgrades
    correlation to a causal law by itself.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
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
                CREATE TABLE IF NOT EXISTS intervention_experiences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at TEXT NOT NULL,
                    transaction_id TEXT NULL,
                    action_name TEXT NOT NULL,
                    arguments_fingerprint TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    observation_id INTEGER NULL,
                    duration_ms REAL NOT NULL,
                    outcome_fingerprint TEXT NOT NULL,
                    outcome_summary TEXT NOT NULL,
                    source TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_intervention_action
                    ON intervention_experiences(action_name, id DESC);
                CREATE INDEX IF NOT EXISTS idx_intervention_transaction
                    ON intervention_experiences(transaction_id, id ASC);
                """
            )

    def record_intervention(
        self,
        *,
        action_name: str,
        arguments: dict[str, Any],
        outcome: Any,
        success: bool,
        duration_ms: float,
        observation_id: int | None = None,
        transaction_id: str | None = None,
        source: str = "runtime",
        outcome_summary: str = "",
    ) -> InterventionExperience:
        action_name = str(action_name).strip()[:128]
        if not action_name:
            raise ValueError("action_name is required")
        args_fp = _fingerprint(arguments)
        outcome_fp = _fingerprint(outcome)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO intervention_experiences(
                    observed_at, transaction_id, action_name, arguments_fingerprint,
                    success, observation_id, duration_ms, outcome_fingerprint,
                    outcome_summary, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now(),
                    str(transaction_id)[:128] if transaction_id else None,
                    action_name,
                    args_fp,
                    1 if success else 0,
                    int(observation_id) if observation_id is not None else None,
                    max(0.0, float(duration_ms)),
                    outcome_fp,
                    str(outcome_summary).strip()[:4000],
                    str(source).strip()[:64] or "runtime",
                ),
            )
            row_id = int(cur.lastrowid)
        item = self.get(row_id)
        if item is None:
            raise RuntimeError("intervention experience disappeared after insert")
        return item

    @staticmethod
    def _from_row(row: sqlite3.Row) -> InterventionExperience:
        return InterventionExperience(
            id=int(row["id"]),
            observed_at=str(row["observed_at"]),
            transaction_id=str(row["transaction_id"]) if row["transaction_id"] else None,
            action_name=str(row["action_name"]),
            arguments_fingerprint=str(row["arguments_fingerprint"]),
            success=bool(row["success"]),
            observation_id=int(row["observation_id"]) if row["observation_id"] is not None else None,
            duration_ms=float(row["duration_ms"]),
            outcome_fingerprint=str(row["outcome_fingerprint"]),
            outcome_summary=str(row["outcome_summary"]),
            source=str(row["source"]),
        )

    def get(self, row_id: int) -> InterventionExperience | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM intervention_experiences WHERE id=?", (int(row_id),)
            ).fetchone()
        return self._from_row(row) if row else None

    def recent(self, limit: int = 32) -> list[InterventionExperience]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM intervention_experiences ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 512)),),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def action_statistics(self, limit: int = 64) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT action_name,
                       COUNT(*) AS attempts,
                       SUM(success) AS successes,
                       AVG(duration_ms) AS avg_duration_ms,
                       MAX(id) AS last_id
                FROM intervention_experiences
                GROUP BY action_name
                ORDER BY attempts DESC, action_name ASC
                LIMIT ?
                """,
                (max(1, min(int(limit), 256)),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            attempts = int(row["attempts"])
            successes = int(row["successes"] or 0)
            result.append(
                {
                    "action_name": str(row["action_name"]),
                    "attempts": attempts,
                    "successes": successes,
                    "success_rate": successes / attempts if attempts else 0.0,
                    "avg_duration_ms": float(row["avg_duration_ms"] or 0.0),
                    "last_experience_id": int(row["last_id"]),
                    "epistemic_status": "empirical_intervention_history_not_causal_proof",
                }
            )
        return result

    def snapshot(self, recent_limit: int = 12) -> dict[str, Any]:
        return {
            "recent_interventions": [item.as_dict() for item in self.recent(recent_limit)],
            "strategy_statistics": self.action_statistics(),
        }
