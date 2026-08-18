from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from .redaction import safe_tool_result


@dataclass(frozen=True, slots=True)
class Forecast:
    id: int
    timestamp: str
    objective: str
    action_name: str
    success_probability: float
    expected_outcome: str
    expected_information_gain: float
    expected_value: float
    unit: str
    resolved: bool
    observed_success: bool | None
    brier_score: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class MetacognitionStore:
    """Persistent forecast/outcome calibration state.

    Forecasts are committed before action execution so later explanations cannot
    retroactively change what ELIA expected. Resolution stores only a redacted result
    descriptor/fingerprint, never raw tool output.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS cognitive_forecasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    action_name TEXT NOT NULL,
                    success_probability REAL NOT NULL,
                    expected_outcome TEXT NOT NULL DEFAULT '',
                    expected_information_gain REAL NOT NULL DEFAULT 0,
                    expected_value REAL NOT NULL DEFAULT 0,
                    unit TEXT NOT NULL DEFAULT 'VALUE_UNIT',
                    context_fingerprint TEXT NOT NULL DEFAULT '',
                    resolved INTEGER NOT NULL DEFAULT 0,
                    resolved_at TEXT NULL,
                    observed_success INTEGER NULL,
                    observation_json TEXT NOT NULL DEFAULT '{}',
                    brier_score REAL NULL
                );
                CREATE INDEX IF NOT EXISTS idx_forecasts_resolved_id
                    ON cognitive_forecasts(resolved, id DESC);
                """
            )

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _probability(value: Any) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("success probability must be finite")
        return max(0.0, min(1.0, number))

    @staticmethod
    def _finite(value: Any, *, field: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{field} must be finite")
        return number

    def record(
        self,
        *,
        objective: str,
        action_name: str,
        success_probability: float,
        expected_outcome: str = "",
        expected_information_gain: float = 0.0,
        expected_value: float = 0.0,
        unit: str = "VALUE_UNIT",
        context_fingerprint: str = "",
    ) -> int:
        probability = self._probability(success_probability)
        information = max(
            0.0,
            self._finite(expected_information_gain, field="expected_information_gain"),
        )
        value = self._finite(expected_value, field="expected_value")
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO cognitive_forecasts(
                    timestamp, objective, action_name, success_probability,
                    expected_outcome, expected_information_gain, expected_value,
                    unit, context_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.now(),
                    str(objective)[:1000],
                    str(action_name)[:128],
                    probability,
                    str(expected_outcome)[:4000],
                    information,
                    value,
                    str(unit)[:64],
                    str(context_fingerprint)[:128],
                ),
            )
            return int(cur.lastrowid)

    def resolve(self, forecast_id: int, *, success: bool, observation: dict[str, Any]) -> float:
        safe_observation = safe_tool_result(observation)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT success_probability, resolved FROM cognitive_forecasts WHERE id=?",
                (int(forecast_id),),
            ).fetchone()
            if row is None:
                raise ValueError(f"forecast does not exist: {forecast_id}")
            if bool(row["resolved"]):
                existing = conn.execute(
                    "SELECT brier_score FROM cognitive_forecasts WHERE id=?",
                    (int(forecast_id),),
                ).fetchone()
                return float(existing["brier_score"])
            p = float(row["success_probability"])
            y = 1.0 if success else 0.0
            brier = (p - y) ** 2
            conn.execute(
                """
                UPDATE cognitive_forecasts
                SET resolved=1, resolved_at=?, observed_success=?, observation_json=?, brier_score=?
                WHERE id=?
                """,
                (
                    self.now(),
                    1 if success else 0,
                    json.dumps(safe_observation, ensure_ascii=False, sort_keys=True)[:12000],
                    brier,
                    int(forecast_id),
                ),
            )
        return brier

    def calibration(self, limit: int = 100) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT success_probability, observed_success, brier_score
                FROM cognitive_forecasts
                WHERE resolved=1
                ORDER BY id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        if not rows:
            return {
                "resolved_forecasts": 0,
                "mean_brier_score": None,
                "predicted_success_mean": None,
                "observed_success_rate": None,
                "calibration_gap": None,
            }
        predicted = sum(float(row["success_probability"]) for row in rows) / len(rows)
        observed = sum(float(row["observed_success"]) for row in rows) / len(rows)
        brier = sum(float(row["brier_score"]) for row in rows) / len(rows)
        return {
            "resolved_forecasts": len(rows),
            "mean_brier_score": brier,
            "predicted_success_mean": predicted,
            "observed_success_rate": observed,
            "calibration_gap": predicted - observed,
        }
