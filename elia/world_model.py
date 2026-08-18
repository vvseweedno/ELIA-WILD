from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(subject: str, predicate: str, obj: Any) -> str:
    return sha256(_canonical([subject, predicate, obj]).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class WorldBelief:
    id: int
    created_at: str
    updated_at: str
    domain: str
    subject: str
    predicate: str
    object: Any
    status: str
    confidence: float
    source: str
    evidence: str
    fingerprint: str
    last_observation_id: int | None
    supersedes_id: int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorldModelStore:
    """Evidence-bearing, revisable model of the external world.

    Model-originated beliefs are hypotheses with confidence caps. Only a trusted
    runtime/adapter can assign `verified` or `refuted`, and that transition requires
    explicit evidence plus a verification authority.
    """

    MODEL_STATUSES = {"hypothesis", "supported", "disputed"}
    TRUSTED_STATUSES = {"verified", "refuted"}
    ALL_STATUSES = MODEL_STATUSES | TRUSTED_STATUSES | {"superseded"}

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS world_beliefs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    last_observation_id INTEGER NULL,
                    supersedes_id INTEGER NULL
                );
                CREATE INDEX IF NOT EXISTS idx_world_beliefs_sp
                    ON world_beliefs(subject, predicate, id DESC);
                CREATE INDEX IF NOT EXISTS idx_world_beliefs_status
                    ON world_beliefs(status, confidence DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_world_beliefs_fp
                    ON world_beliefs(fingerprint, id DESC);

                CREATE TABLE IF NOT EXISTS world_belief_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    belief_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    event TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    authority TEXT NULL,
                    FOREIGN KEY(belief_id) REFERENCES world_beliefs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_world_belief_events
                    ON world_belief_events(belief_id, id ASC);
                """
            )

    @staticmethod
    def _clean_text(value: Any, field: str, maximum: int) -> str:
        text = str(value).strip()[:maximum]
        if not text:
            raise ValueError(f"{field} is required")
        return text

    @staticmethod
    def _confidence(value: Any, cap: float = 1.0) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("confidence must be finite")
        return max(0.0, min(float(cap), number))

    @staticmethod
    def _from_row(row: sqlite3.Row) -> WorldBelief:
        return WorldBelief(
            id=int(row["id"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            domain=str(row["domain"]),
            subject=str(row["subject"]),
            predicate=str(row["predicate"]),
            object=json.loads(row["object_json"]),
            status=str(row["status"]),
            confidence=float(row["confidence"]),
            source=str(row["source"]),
            evidence=str(row["evidence"]),
            fingerprint=str(row["fingerprint"]),
            last_observation_id=(
                int(row["last_observation_id"])
                if row["last_observation_id"] is not None
                else None
            ),
            supersedes_id=int(row["supersedes_id"]) if row["supersedes_id"] is not None else None,
        )

    def get(self, belief_id: int) -> WorldBelief | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM world_beliefs WHERE id=?", (int(belief_id),)).fetchone()
        return self._from_row(row) if row else None

    def propose(
        self,
        *,
        domain: str,
        subject: str,
        predicate: str,
        object: Any,
        confidence: float,
        evidence: str,
        source: str = "brain",
        observation_id: int | None = None,
    ) -> WorldBelief:
        domain = self._clean_text(domain, "domain", 128)
        subject = self._clean_text(subject, "subject", 512)
        predicate = self._clean_text(predicate, "predicate", 256)
        evidence = self._clean_text(evidence, "evidence", 8000)
        object_json = _canonical(object)
        fp = _fingerprint(subject, predicate, object)
        confidence = self._confidence(confidence, 0.75 if source == "brain" else 0.90)
        timestamp = _now()

        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM world_beliefs
                WHERE fingerprint=? AND status NOT IN ('refuted', 'superseded')
                ORDER BY id DESC LIMIT 1
                """,
                (fp,),
            ).fetchone()
            if existing is not None:
                current = self._from_row(existing)
                next_confidence = max(current.confidence, confidence)
                conn.execute(
                    """
                    UPDATE world_beliefs
                    SET updated_at=?, confidence=?, evidence=?, last_observation_id=?
                    WHERE id=?
                    """,
                    (
                        timestamp,
                        next_confidence,
                        evidence,
                        int(observation_id) if observation_id is not None else current.last_observation_id,
                        current.id,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO world_belief_events(belief_id, timestamp, event, evidence, authority)
                    VALUES (?, ?, 'additional_evidence', ?, NULL)
                    """,
                    (current.id, timestamp, evidence),
                )
                belief_id = current.id
            else:
                cur = conn.execute(
                    """
                    INSERT INTO world_beliefs(
                        created_at, updated_at, domain, subject, predicate, object_json,
                        status, confidence, source, evidence, fingerprint,
                        last_observation_id, supersedes_id
                    ) VALUES (?, ?, ?, ?, ?, ?, 'hypothesis', ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        timestamp,
                        timestamp,
                        domain,
                        subject,
                        predicate,
                        object_json,
                        confidence,
                        str(source).strip()[:64] or "brain",
                        evidence,
                        fp,
                        int(observation_id) if observation_id is not None else None,
                    ),
                )
                belief_id = int(cur.lastrowid)
                conn.execute(
                    """
                    INSERT INTO world_belief_events(belief_id, timestamp, event, evidence, authority)
                    VALUES (?, ?, 'proposed', ?, NULL)
                    """,
                    (belief_id, timestamp, evidence),
                )
        belief = self.get(belief_id)
        if belief is None:
            raise RuntimeError("world belief disappeared after proposal")
        return belief

    def revise_from_model(
        self,
        belief_id: int,
        *,
        status: str | None = None,
        confidence: float | None = None,
        evidence: str,
        observation_id: int | None = None,
    ) -> WorldBelief:
        current = self.get(belief_id)
        if current is None:
            raise ValueError(f"unknown world belief: {belief_id}")
        next_status = current.status if status is None else str(status).strip().lower()
        if next_status not in self.MODEL_STATUSES:
            raise ValueError("model-originated revision may only use hypothesis/supported/disputed")
        evidence = self._clean_text(evidence, "evidence", 8000)
        next_confidence = (
            current.confidence
            if confidence is None
            else self._confidence(confidence, 0.90)
        )
        timestamp = _now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE world_beliefs
                SET updated_at=?, status=?, confidence=?, evidence=?, last_observation_id=?
                WHERE id=?
                """,
                (
                    timestamp,
                    next_status,
                    next_confidence,
                    evidence,
                    int(observation_id) if observation_id is not None else current.last_observation_id,
                    current.id,
                ),
            )
            conn.execute(
                """
                INSERT INTO world_belief_events(belief_id, timestamp, event, evidence, authority)
                VALUES (?, ?, 'model_revision', ?, NULL)
                """,
                (current.id, timestamp, evidence),
            )
        updated = self.get(current.id)
        if updated is None:
            raise RuntimeError("world belief disappeared after revision")
        return updated

    def adjudicate(
        self,
        belief_id: int,
        *,
        status: str,
        confidence: float,
        evidence: str,
        authority: str,
        observation_id: int | None = None,
    ) -> WorldBelief:
        current = self.get(belief_id)
        if current is None:
            raise ValueError(f"unknown world belief: {belief_id}")
        status = str(status).strip().lower()
        if status not in self.TRUSTED_STATUSES:
            raise ValueError("trusted adjudication status must be verified or refuted")
        evidence = self._clean_text(evidence, "evidence", 8000)
        authority = self._clean_text(authority, "authority", 256)
        confidence = self._confidence(confidence, 1.0)
        timestamp = _now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE world_beliefs
                SET updated_at=?, status=?, confidence=?, evidence=?, last_observation_id=?
                WHERE id=?
                """,
                (
                    timestamp,
                    status,
                    confidence,
                    evidence,
                    int(observation_id) if observation_id is not None else current.last_observation_id,
                    current.id,
                ),
            )
            conn.execute(
                """
                INSERT INTO world_belief_events(belief_id, timestamp, event, evidence, authority)
                VALUES (?, ?, 'trusted_adjudication', ?, ?)
                """,
                (current.id, timestamp, evidence, authority),
            )
        updated = self.get(current.id)
        if updated is None:
            raise RuntimeError("world belief disappeared after adjudication")
        return updated

    def query(
        self,
        *,
        text: str = "",
        domain: str | None = None,
        statuses: set[str] | None = None,
        limit: int = 32,
    ) -> list[WorldBelief]:
        clauses = ["1=1"]
        params: list[Any] = []
        if domain:
            clauses.append("domain=?")
            params.append(str(domain))
        if statuses:
            clean = sorted(set(statuses) & self.ALL_STATUSES)
            if clean:
                clauses.append("status IN (" + ",".join("?" for _ in clean) + ")")
                params.extend(clean)
        if text.strip():
            needle = f"%{text.strip()}%"
            clauses.append("(subject LIKE ? OR predicate LIKE ? OR object_json LIKE ? OR evidence LIKE ?)")
            params.extend([needle, needle, needle, needle])
        params.append(max(1, min(int(limit), 256)))
        sql = (
            "SELECT * FROM world_beliefs WHERE "
            + " AND ".join(clauses)
            + " ORDER BY CASE status WHEN 'verified' THEN 0 WHEN 'supported' THEN 1 "
              "WHEN 'hypothesis' THEN 2 WHEN 'disputed' THEN 3 ELSE 4 END, "
              "confidence DESC, id DESC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._from_row(row) for row in rows]

    def contradictions(self, subject: str, predicate: str) -> list[WorldBelief]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM world_beliefs
                WHERE subject=? AND predicate=?
                  AND status IN ('hypothesis', 'supported', 'disputed', 'verified')
                ORDER BY confidence DESC, id DESC
                """,
                (str(subject), str(predicate)),
            ).fetchall()
        beliefs = [self._from_row(row) for row in rows]
        distinct = {_canonical(item.object) for item in beliefs}
        return beliefs if len(distinct) > 1 else []

    def snapshot(self, limit: int = 24) -> dict[str, Any]:
        beliefs = self.query(statuses={"verified", "supported", "hypothesis", "disputed"}, limit=limit)
        contradictions: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for belief in beliefs:
            key = (belief.subject, belief.predicate)
            if key in seen:
                continue
            seen.add(key)
            conflicting = self.contradictions(*key)
            if conflicting:
                contradictions.append(
                    {
                        "subject": key[0],
                        "predicate": key[1],
                        "belief_ids": [item.id for item in conflicting],
                    }
                )
        return {
            "beliefs": [item.as_dict() for item in beliefs],
            "contradictions": contradictions[:16],
            "epistemic_rule": "beliefs are revisable; model hypotheses are not verified facts",
        }
