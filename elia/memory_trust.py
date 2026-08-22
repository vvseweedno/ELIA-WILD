from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from .memory import MemoryRecord, MemoryStore


TRUST_SCORES: dict[str, float] = {
    "untrusted_observation": 0.15,
    "brain_hypothesis": 0.25,
    "corroborated_memory": 0.55,
    "causal_evidence": 0.75,
    "verified_fact": 0.90,
    "protected_identity": 1.00,
}

PROMOTABLE = frozenset({"corroborated_memory", "causal_evidence"})


def memory_trust_class(record: MemoryRecord) -> str:
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    declared = str(metadata.get("trust_class", "")).strip()
    if declared in TRUST_SCORES:
        return declared
    source = str(record.source).strip().lower()
    if source == "brain":
        return "brain_hypothesis"
    if source in {"continuity_kernel", "verification_kernel", "owner_control"}:
        return "verified_fact"
    if source in {"runtime", "resource_ingress_registry", "work_port_registry"}:
        return "causal_evidence"
    return "untrusted_observation"


def memory_trust_score(record: MemoryRecord) -> float:
    return TRUST_SCORES[memory_trust_class(record)]


@dataclass(frozen=True, slots=True)
class MemoryPromotion:
    memory_id: int
    from_class: str
    to_class: str
    evidence: str
    authority: str
    promoted_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "from_class": self.from_class,
            "to_class": self.to_class,
            "evidence": self.evidence,
            "authority": self.authority,
            "promoted_at": self.promoted_at,
        }


class MemoryTrustGate:
    """Trust transition boundary for persistent autobiographical memory.

    Model-authored memory is useful as a hypothesis, never as authority. The model has
    no method to promote a memory. Generic promotion can only reach bounded local
    corroboration/causal-evidence classes; a `verified_fact` must come from a
    domain-specific authenticated verifier rather than a caller-supplied authority
    string. Protected identity remains outside this mutable memory gate and is governed
    by Subject Core/Constitution fingerprints.
    """

    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory
        self.path = Path(memory.path)
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
                CREATE TABLE IF NOT EXISTS memory_trust_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL,
                    from_class TEXT NOT NULL,
                    to_class TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    authority TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_memory_trust_events_memory
                    ON memory_trust_events(memory_id, id ASC);
                """
            )

    def remember_from_brain(
        self,
        item: dict[str, Any],
        *,
        identity_fingerprint: str,
        model_id: str,
    ) -> int | None:
        content = str(item.get("content", "")).strip()
        if not content:
            return None
        claimed_kind = str(item.get("kind", "lesson")).strip()[:64] or "lesson"
        try:
            requested_importance = float(item.get("importance", 0.5))
        except (TypeError, ValueError):
            requested_importance = 0.5
        importance = max(0.0, min(0.65, requested_importance))
        return self.memory.remember(
            "brain_hypothesis",
            content[:8000],
            importance=importance,
            source="brain",
            metadata={
                "trust_class": "brain_hypothesis",
                "claimed_kind": claimed_kind,
                "identity_fingerprint": str(identity_fingerprint),
                "model_id": str(model_id),
                "influence_cap": 0.65,
            },
        )

    def promote(
        self,
        memory_id: int,
        *,
        to_class: str,
        evidence: str,
        authority: str,
    ) -> MemoryPromotion:
        target = str(to_class).strip()
        if target not in PROMOTABLE:
            raise ValueError(
                "generic memory promotion may only reach corroborated_memory or causal_evidence; verified_fact requires a domain-specific authenticated verifier"
            )
        evidence = str(evidence).strip()[:8000]
        authority = str(authority).strip()[:256]
        if not evidence or not authority:
            raise ValueError("memory trust promotion requires evidence and authority")
        timestamp = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT source, metadata_json FROM memories WHERE id=?",
                (int(memory_id),),
            ).fetchone()
            if row is None:
                raise ValueError(f"memory does not exist: {memory_id}")
            metadata = json.loads(str(row["metadata_json"]) or "{}")
            fake_record = MemoryRecord(
                id=int(memory_id),
                timestamp="",
                kind="",
                content="",
                importance=0.0,
                source=str(row["source"]),
                metadata=metadata,
            )
            current = memory_trust_class(fake_record)
            if TRUST_SCORES[target] <= TRUST_SCORES[current]:
                raise ValueError(
                    f"memory trust promotion must strictly increase trust: {current} -> {target}"
                )
            metadata["trust_class"] = target
            metadata["trust_evidence"] = evidence
            metadata["trust_authority"] = authority
            metadata["trust_promoted_at"] = timestamp
            conn.execute(
                "UPDATE memories SET metadata_json=? WHERE id=?",
                (json.dumps(metadata, ensure_ascii=False, sort_keys=True), int(memory_id)),
            )
            conn.execute(
                """
                INSERT INTO memory_trust_events(
                    memory_id, from_class, to_class, evidence, authority, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (int(memory_id), current, target, evidence, authority, timestamp),
            )
        return MemoryPromotion(
            memory_id=int(memory_id),
            from_class=current,
            to_class=target,
            evidence=evidence,
            authority=authority,
            promoted_at=timestamp,
        )
