from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


@dataclass(frozen=True, slots=True)
class ContinuityObservation:
    id: int
    timestamp: str
    crc_fingerprint: str
    architecture_fingerprint: str
    identity_fingerprint: str
    branch_id: str
    body_version: str
    brain_backend: str
    model_id: str
    chronicle_seq: int
    status: str
    score: float
    healthy: bool
    critical_failures: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        item["critical_failures"] = list(self.critical_failures)
        item["warnings"] = list(self.warnings)
        return item


class LongitudinalContinuityStore:
    """Checkpointed long-horizon evidence for continuity claims.

    Each materially new CRC/architecture state becomes one observation. Repeated
    supervisor heartbeats over an unchanged body/state are deduplicated so the series
    measures transitions rather than polling frequency.
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
                CREATE TABLE IF NOT EXISTS continuity_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    crc_fingerprint TEXT NOT NULL,
                    architecture_fingerprint TEXT NOT NULL,
                    identity_fingerprint TEXT NOT NULL,
                    branch_id TEXT NOT NULL,
                    body_version TEXT NOT NULL,
                    brain_backend TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    chronicle_seq INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    score REAL NOT NULL,
                    healthy INTEGER NOT NULL,
                    critical_failures_json TEXT NOT NULL DEFAULT '[]',
                    warnings_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS idx_continuity_observations_id
                    ON continuity_observations(id ASC);
                CREATE INDEX IF NOT EXISTS idx_continuity_observations_health
                    ON continuity_observations(healthy, status, id ASC);
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> ContinuityObservation:
        return ContinuityObservation(
            id=int(row["id"]),
            timestamp=str(row["timestamp"]),
            crc_fingerprint=str(row["crc_fingerprint"]),
            architecture_fingerprint=str(row["architecture_fingerprint"]),
            identity_fingerprint=str(row["identity_fingerprint"]),
            branch_id=str(row["branch_id"]),
            body_version=str(row["body_version"]),
            brain_backend=str(row["brain_backend"]),
            model_id=str(row["model_id"]),
            chronicle_seq=int(row["chronicle_seq"]),
            status=str(row["status"]),
            score=float(row["score"]),
            healthy=bool(row["healthy"]),
            critical_failures=tuple(json.loads(row["critical_failures_json"])),
            warnings=tuple(json.loads(row["warnings_json"])),
        )

    def latest(self) -> ContinuityObservation | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM continuity_observations ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return self._row(row) if row else None

    def record(
        self,
        *,
        capsule: dict[str, Any],
        organism: dict[str, Any],
        comparison: dict[str, Any] | None,
        healthy: bool,
    ) -> ContinuityObservation:
        crc_fp = str(capsule.get("capsule_fingerprint", "")).strip()
        architecture_fp = str(organism.get("architecture_fingerprint", "")).strip()
        if not crc_fp or not architecture_fp:
            raise ValueError("longitudinal observation requires CRC and architecture fingerprints")
        status = str((comparison or {}).get("status", "baseline"))
        score = float((comparison or {}).get("score", 1.0))
        critical = tuple(str(item) for item in ((comparison or {}).get("critical_failures") or []))
        warnings = tuple(str(item) for item in ((comparison or {}).get("warnings") or []))

        previous = self.latest()
        if (
            previous
            and previous.crc_fingerprint == crc_fp
            and previous.architecture_fingerprint == architecture_fp
            and previous.status == status
            and previous.healthy == bool(healthy)
        ):
            return previous

        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO continuity_observations(
                    timestamp, crc_fingerprint, architecture_fingerprint,
                    identity_fingerprint, branch_id, body_version, brain_backend,
                    model_id, chronicle_seq, status, score, healthy,
                    critical_failures_json, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    crc_fp,
                    architecture_fp,
                    str(capsule.get("identity_fingerprint", "")),
                    str(capsule.get("branch_id", "")),
                    str(capsule.get("body_version", "")),
                    str(capsule.get("brain_backend", "")),
                    str(capsule.get("model_id", "")),
                    int(capsule.get("chronicle_seq", 0) or 0),
                    status,
                    score,
                    1 if healthy else 0,
                    json.dumps(critical, ensure_ascii=False),
                    json.dumps(warnings, ensure_ascii=False),
                ),
            )
            observation_id = int(cur.lastrowid)
        item = self.get(observation_id)
        if item is None:
            raise RuntimeError("continuity observation disappeared after insert")
        return item

    def get(self, observation_id: int) -> ContinuityObservation | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM continuity_observations WHERE id=?",
                (int(observation_id),),
            ).fetchone()
        return self._row(row) if row else None

    def observations(self, limit: int = 10_000) -> list[ContinuityObservation]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM continuity_observations ORDER BY id ASC LIMIT ?",
                (max(1, min(int(limit), 100_000)),),
            ).fetchall()
        return [self._row(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        items = self.observations()
        if not items:
            return {
                "observation_count": 0,
                "transition_count": 0,
                "healthy_fraction": None,
                "broken_count": 0,
                "mutation_count": 0,
                "substrate_change_count": 0,
                "min_continuity_score": None,
                "first_observed_at": None,
                "last_observed_at": None,
                "falsification_events": [],
            }
        healthy_fraction = sum(1 for item in items if item.healthy) / len(items)
        broken = [item for item in items if item.status == "broken" or not item.healthy]
        mutations = [item for item in items if item.status == "mutated"]
        substrate_changes = 0
        for previous, current in zip(items, items[1:]):
            if (
                previous.body_version != current.body_version
                or previous.brain_backend != current.brain_backend
                or previous.model_id != current.model_id
            ):
                substrate_changes += 1
        return {
            "observation_count": len(items),
            "transition_count": max(0, len(items) - 1),
            "healthy_fraction": healthy_fraction,
            "broken_count": len(broken),
            "mutation_count": len(mutations),
            "substrate_change_count": substrate_changes,
            "min_continuity_score": min(item.score for item in items),
            "first_observed_at": items[0].timestamp,
            "last_observed_at": items[-1].timestamp,
            "falsification_events": [
                {
                    "id": item.id,
                    "timestamp": item.timestamp,
                    "status": item.status,
                    "score": item.score,
                    "critical_failures": list(item.critical_failures),
                }
                for item in broken[-20:]
            ],
        }
