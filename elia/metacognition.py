from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from .redaction import safe_tool_result
from .sqlite_utils import inserted_row_id


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
    execution_ok: bool | None = None
    resolution_basis: str = "unresolved"

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
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(cognitive_forecasts)").fetchall()
            }
            if "execution_ok" not in columns:
                conn.execute(
                    "ALTER TABLE cognitive_forecasts ADD COLUMN execution_ok INTEGER NULL"
                )
            if "resolution_basis" not in columns:
                conn.execute(
                    "ALTER TABLE cognitive_forecasts ADD COLUMN resolution_basis TEXT "
                    "NOT NULL DEFAULT 'legacy_execution_proxy'"
                )

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _probability(value: Any) -> float:
        if isinstance(value, bool):
            raise ValueError("success probability must be a numeric value in [0, 1]")
        number = float(value)
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            raise ValueError("success probability must be finite and within [0, 1]")
        return number

    @staticmethod
    def _finite(value: Any, *, field: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{field} must be numeric")
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
        information = self._finite(
            expected_information_gain,
            field="expected_information_gain",
        )
        if information < 0.0:
            raise ValueError("expected_information_gain must be non-negative")
        value = self._finite(expected_value, field="expected_value")
        unit_text = str(unit).strip()[:64]
        if not unit_text:
            raise ValueError("forecast value unit is required")
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
                    unit_text,
                    str(context_fingerprint)[:128],
                ),
            )
            return inserted_row_id(cur, operation="forecast insert")

    def resolve(
        self,
        forecast_id: int,
        *,
        success: bool,
        observation: dict[str, Any],
        outcome_success: bool | None = None,
    ) -> float:
        """Resolve a forecast while separating execution from intended outcome.

        Existing runtime callers supply ``success=result.ok``. Unless a trusted caller
        also supplies an explicit outcome boolean, that is
        recorded as an ``execution_proxy`` and excluded from outcome-only calibration.
        Arbitrary tool/provider payload keys are deliberately not treated as outcome
        labels because that would let the observed system label its own forecast.
        """

        if not isinstance(success, bool):
            raise TypeError("success must be bool")
        safe_observation = safe_tool_result(observation)
        explicit = outcome_success
        if explicit is not None and not isinstance(explicit, bool):
            raise TypeError("outcome_success must be bool or None")
        resolution_basis = "observed_outcome" if explicit is not None else "execution_proxy"
        resolved_success = bool(explicit) if explicit is not None else bool(success)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT success_probability, resolved, observed_success, brier_score, "
                "execution_ok, resolution_basis FROM cognitive_forecasts WHERE id=?",
                (int(forecast_id),),
            ).fetchone()
            if row is None:
                raise ValueError(f"forecast does not exist: {forecast_id}")
            if bool(row["resolved"]):
                existing_success = bool(row["observed_success"])
                if (
                    existing_success != resolved_success
                    or bool(row["execution_ok"]) != success
                    or str(row["resolution_basis"]) != resolution_basis
                ):
                    raise ValueError("forecast is already resolved with a different outcome")
                return float(row["brier_score"])
            p = self._probability(row["success_probability"])
            y = 1.0 if resolved_success else 0.0
            brier = (p - y) ** 2
            conn.execute(
                """
                UPDATE cognitive_forecasts
                SET resolved=1, resolved_at=?, observed_success=?, observation_json=?,
                    brier_score=?, execution_ok=?, resolution_basis=?
                WHERE id=?
                """,
                (
                    self.now(),
                    1 if resolved_success else 0,
                    json.dumps(
                        safe_observation,
                        ensure_ascii=False,
                        sort_keys=True,
                        allow_nan=False,
                    )[:12000],
                    brier,
                    1 if success else 0,
                    resolution_basis,
                    int(forecast_id),
                ),
            )
        return brier

    def calibration(self, limit: int = 100) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT success_probability, observed_success, brier_score,
                       execution_ok, resolution_basis
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
                "resolution_scope": "none",
                "outcome_calibration": self._calibration_summary([]),
                "execution_proxy_forecasts": 0,
                "claim_safe_scope": "explicit_outcomes_only",
            }
        predicted_values = [self._probability(row["success_probability"]) for row in rows]
        observed_values = [self._binary(row["observed_success"]) for row in rows]
        brier_values = [self._brier(row["brier_score"]) for row in rows]
        predicted = sum(predicted_values) / len(rows)
        observed = sum(observed_values) / len(rows)
        brier = sum(brier_values) / len(rows)
        return {
            "resolved_forecasts": len(rows),
            "mean_brier_score": brier,
            "predicted_success_mean": predicted,
            "observed_success_rate": observed,
            "calibration_gap": predicted - observed,
            "resolution_scope": "all_resolutions_including_execution_proxies",
            "outcome_calibration": self._calibration_summary(
                [row for row in rows if str(row["resolution_basis"]) == "observed_outcome"]
            ),
            "execution_proxy_forecasts": sum(
                1 for row in rows if str(row["resolution_basis"]) != "observed_outcome"
            ),
            "claim_safe_scope": "explicit_outcomes_only",
        }

    @staticmethod
    def _binary(value: Any) -> float:
        if value not in (0, 1, False, True):
            raise ValueError("persisted forecast outcome must be binary")
        return 1.0 if bool(value) else 0.0

    @classmethod
    def _brier(cls, value: Any) -> float:
        score = cls._finite(value, field="persisted Brier score")
        if not 0.0 <= score <= 1.0:
            raise ValueError("persisted Brier score must be within [0, 1]")
        return score

    @staticmethod
    def _calibration_summary(rows: list[sqlite3.Row]) -> dict[str, Any]:
        """Outcome-only calibration with an honest small-sample indicator."""

        if not rows:
            return {
                "resolved_outcomes": 0,
                "mean_brier_score": None,
                "predicted_success_mean": None,
                "observed_success_rate": None,
                "calibration_gap": None,
                "observed_rate_wilson_95": None,
                "evidence_status": "no_explicit_outcomes",
                "descriptive_reporting_threshold": 30,
                "powered_claim_supported": False,
            }
        count = len(rows)
        predicted = sum(
            MetacognitionStore._probability(row["success_probability"])
            for row in rows
        ) / count
        successes = sum(
            MetacognitionStore._binary(row["observed_success"]) for row in rows
        )
        observed = successes / count
        brier = sum(MetacognitionStore._brier(row["brier_score"]) for row in rows) / count
        z = 1.959963984540054
        denominator = 1.0 + z * z / count
        center = (observed + z * z / (2.0 * count)) / denominator
        margin = (
            z
            * math.sqrt(
                observed * (1.0 - observed) / count
                + z * z / (4.0 * count * count)
            )
            / denominator
        )
        return {
            "resolved_outcomes": count,
            "mean_brier_score": brier,
            "predicted_success_mean": predicted,
            "observed_success_rate": observed,
            "calibration_gap": predicted - observed,
            "observed_rate_wilson_95": [
                max(0.0, center - margin),
                min(1.0, center + margin),
            ],
            "evidence_status": (
                "descriptive_small_sample" if count < 30 else "descriptive"
            ),
            "descriptive_reporting_threshold": 30,
            "powered_claim_supported": False,
            "statistical_scope": (
                "Wilson interval describes only the binary outcome rate; no confidence "
                "interval or hypothesis test for calibration performance is claimed."
            ),
        }
