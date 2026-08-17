from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import sqlite3
from typing import Any


@dataclass(frozen=True, slots=True)
class SelfHypothesis:
    id: int
    created_at: str
    updated_at: str
    domain: str
    proposition: str
    confidence: float
    status: str
    evidence: str
    source: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class SelfHypothesisStore:
    """Adaptive self-model claims kept separate from immutable Subject Core.

    A hypothesis can describe capability, preference, strategy, limitation or identity
    interpretation, but it remains an evidence-bearing revisable claim. This avoids
    encoding every autobiographical model statement as a permanent identity invariant.
    """

    STATUSES = {"active", "supported", "uncertain", "refuted", "retired"}
    DOMAINS = {
        "capability",
        "preference",
        "strategy",
        "limitation",
        "relationship",
        "identity_interpretation",
        "uncertainty",
        "other",
    }

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
                CREATE TABLE IF NOT EXISTS self_hypotheses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    proposition TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    status TEXT NOT NULL DEFAULT 'active',
                    evidence TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'brain'
                );
                CREATE INDEX IF NOT EXISTS idx_self_hypotheses_status_conf
                    ON self_hypotheses(status, confidence DESC, id ASC);

                CREATE TABLE IF NOT EXISTS self_hypothesis_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hypothesis_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(hypothesis_id) REFERENCES self_hypotheses(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_self_hypothesis_events_h
                    ON self_hypothesis_events(hypothesis_id, id ASC);
                """
            )

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _confidence(value: Any) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("self-hypothesis confidence must be finite")
        return max(0.0, min(1.0, number))

    def create(
        self,
        *,
        domain: str,
        proposition: str,
        confidence: float,
        evidence: str,
        source: str = "brain",
    ) -> int:
        domain = str(domain).strip().lower()
        if domain not in self.DOMAINS:
            raise ValueError(f"invalid self-hypothesis domain: {domain}")
        proposition = str(proposition).strip()[:4000]
        evidence = str(evidence).strip()[:8000]
        if not proposition:
            raise ValueError("self-hypothesis proposition is required")
        if not evidence:
            raise ValueError("self-hypothesis requires evidence")
        confidence = self._confidence(confidence)
        timestamp = self.now()
        with self._connect() as conn:
            duplicate = conn.execute(
                """
                SELECT id FROM self_hypotheses
                WHERE lower(proposition)=lower(?) AND status NOT IN ('refuted','retired')
                LIMIT 1
                """,
                (proposition,),
            ).fetchone()
            if duplicate is not None:
                return int(duplicate["id"])
            cur = conn.execute(
                """
                INSERT INTO self_hypotheses(
                    created_at, updated_at, domain, proposition, confidence,
                    status, evidence, source
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    timestamp,
                    timestamp,
                    domain,
                    proposition,
                    confidence,
                    evidence,
                    str(source)[:64],
                ),
            )
            hypothesis_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO self_hypothesis_events(
                    hypothesis_id, timestamp, kind, confidence, evidence
                ) VALUES (?, ?, 'created', ?, ?)
                """,
                (hypothesis_id, timestamp, confidence, evidence),
            )
            return hypothesis_id

    @staticmethod
    def _from_row(row: sqlite3.Row) -> SelfHypothesis:
        return SelfHypothesis(
            id=int(row["id"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            domain=str(row["domain"]),
            proposition=str(row["proposition"]),
            confidence=float(row["confidence"]),
            status=str(row["status"]),
            evidence=str(row["evidence"]),
            source=str(row["source"]),
        )

    def get(self, hypothesis_id: int) -> SelfHypothesis | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM self_hypotheses WHERE id=?", (int(hypothesis_id),)
            ).fetchone()
        return self._from_row(row) if row else None

    def active(self, limit: int = 32) -> list[SelfHypothesis]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM self_hypotheses
                WHERE status IN ('active','supported','uncertain')
                ORDER BY confidence DESC, id ASC
                LIMIT ?
                """,
                (max(1, min(int(limit), 256)),),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def update(
        self,
        hypothesis_id: int,
        *,
        confidence: float | None = None,
        status: str | None = None,
        evidence: str,
        event: str = "updated",
    ) -> SelfHypothesis:
        current = self.get(hypothesis_id)
        if current is None:
            raise ValueError(f"self-hypothesis does not exist: {hypothesis_id}")
        next_status = current.status if status is None else str(status).strip().lower()
        if next_status not in self.STATUSES:
            raise ValueError(f"invalid self-hypothesis status: {next_status}")
        evidence = str(evidence).strip()[:8000]
        if not evidence:
            raise ValueError("self-hypothesis update requires evidence")
        next_confidence = current.confidence if confidence is None else self._confidence(confidence)
        timestamp = self.now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE self_hypotheses
                SET updated_at=?, confidence=?, status=?, evidence=?
                WHERE id=?
                """,
                (
                    timestamp,
                    next_confidence,
                    next_status,
                    evidence,
                    int(hypothesis_id),
                ),
            )
            conn.execute(
                """
                INSERT INTO self_hypothesis_events(
                    hypothesis_id, timestamp, kind, confidence, evidence
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(hypothesis_id),
                    timestamp,
                    str(event)[:64],
                    next_confidence,
                    evidence,
                ),
            )
        updated = self.get(hypothesis_id)
        if updated is None:
            raise RuntimeError("self-hypothesis disappeared after update")
        return updated

    def snapshot(self, limit: int = 24) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self.active(limit)]
