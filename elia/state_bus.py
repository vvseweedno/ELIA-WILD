from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _event_hash(
    transaction_id: str,
    seq: int,
    timestamp: str,
    phase: str,
    kind: str,
    payload_json: str,
    prev_hash: str,
) -> str:
    material = "\n".join(
        [transaction_id, str(seq), timestamp, phase, kind, payload_json, prev_hash]
    )
    return sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BusEvent:
    id: int
    transaction_id: str
    seq: int
    timestamp: str
    phase: str
    kind: str
    payload: dict[str, Any]
    prev_hash: str
    event_hash: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class OrganismStateBus:
    """Durable causal transition journal for organism activity.

    External effects cannot be rolled back by SQLite, but every local transition is
    serialized with BEGIN IMMEDIATE so event sequencing and terminal state changes
    cannot split across competing writers or ordinary process interruption.
    """

    TERMINAL = {"committed", "aborted"}

    def __init__(self, path: Path):
        self.path = Path(path)
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
                CREATE TABLE IF NOT EXISTS organism_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NULL,
                    status TEXT NOT NULL,
                    label TEXT NOT NULL,
                    identity_fingerprint TEXT NOT NULL DEFAULT '',
                    parent_transaction_id TEXT NULL
                );
                CREATE TABLE IF NOT EXISTS organism_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    FOREIGN KEY(transaction_id)
                        REFERENCES organism_transactions(transaction_id)
                        ON DELETE CASCADE,
                    UNIQUE(transaction_id, seq)
                );
                CREATE INDEX IF NOT EXISTS idx_organism_tx_status
                    ON organism_transactions(status, started_at);
                CREATE INDEX IF NOT EXISTS idx_organism_event_tx
                    ON organism_events(transaction_id, seq ASC);
                """
            )

    @staticmethod
    def _require_open(conn: sqlite3.Connection, transaction_id: str) -> None:
        row = conn.execute(
            "SELECT status FROM organism_transactions WHERE transaction_id=?",
            (transaction_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown organism transaction: {transaction_id}")
        if str(row["status"]) != "open":
            raise RuntimeError("cannot append to a closed organism transaction")

    def _append_locked(
        self,
        conn: sqlite3.Connection,
        transaction_id: str,
        *,
        phase: str,
        kind: str,
        payload: dict[str, Any] | None = None,
    ) -> BusEvent:
        self._require_open(conn, transaction_id)
        payload_json = _canonical(payload or {})
        timestamp = _now()
        phase = str(phase).strip()[:64]
        kind = str(kind).strip()[:128]
        if not phase or not kind:
            raise ValueError("phase and kind are required")
        previous = conn.execute(
            """
            SELECT seq, event_hash FROM organism_events
            WHERE transaction_id=? ORDER BY seq DESC LIMIT 1
            """,
            (transaction_id,),
        ).fetchone()
        seq = int(previous["seq"]) + 1 if previous else 1
        prev_hash = str(previous["event_hash"]) if previous else "0" * 64
        event_hash = _event_hash(
            transaction_id, seq, timestamp, phase, kind, payload_json, prev_hash
        )
        cur = conn.execute(
            """
            INSERT INTO organism_events(
                transaction_id, seq, timestamp, phase, kind, payload_json,
                prev_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transaction_id,
                seq,
                timestamp,
                phase,
                kind,
                payload_json,
                prev_hash,
                event_hash,
            ),
        )
        return BusEvent(
            int(cur.lastrowid),
            transaction_id,
            seq,
            timestamp,
            phase,
            kind,
            json.loads(payload_json),
            prev_hash,
            event_hash,
        )

    def begin(
        self,
        label: str,
        *,
        identity_fingerprint: str = "",
        parent_transaction_id: str | None = None,
        transaction_id: str | None = None,
    ) -> str:
        transaction_id = transaction_id or uuid4().hex
        label = str(label).strip()[:128]
        if not label:
            raise ValueError("transaction label is required")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO organism_transactions(
                    transaction_id, started_at, status, label,
                    identity_fingerprint, parent_transaction_id
                ) VALUES (?, ?, 'open', ?, ?, ?)
                """,
                (
                    transaction_id,
                    _now(),
                    label,
                    str(identity_fingerprint)[:128],
                    str(parent_transaction_id)[:128] if parent_transaction_id else None,
                ),
            )
            self._append_locked(
                conn,
                transaction_id,
                phase="begin",
                kind="TRANSACTION_BEGIN",
                payload={"label": label},
            )
            conn.commit()
        return transaction_id

    def append(
        self,
        transaction_id: str,
        *,
        phase: str,
        kind: str,
        payload: dict[str, Any] | None = None,
    ) -> BusEvent:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            event = self._append_locked(
                conn,
                transaction_id,
                phase=phase,
                kind=kind,
                payload=payload,
            )
            conn.commit()
            return event

    def _finish(self, transaction_id: str, status: str, payload: dict[str, Any]) -> BusEvent:
        if status not in self.TERMINAL:
            raise ValueError(f"invalid terminal state: {status}")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            event = self._append_locked(
                conn,
                transaction_id,
                phase="end",
                kind="TRANSACTION_COMMIT" if status == "committed" else "TRANSACTION_ABORT",
                payload=payload,
            )
            cur = conn.execute(
                """
                UPDATE organism_transactions
                SET status=?, finished_at=? WHERE transaction_id=? AND status='open'
                """,
                (status, _now(), transaction_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError("transaction terminal state changed concurrently")
            conn.commit()
            return event

    def commit(self, transaction_id: str, payload: dict[str, Any] | None = None) -> BusEvent:
        return self._finish(transaction_id, "committed", payload or {})

    def abort(self, transaction_id: str, reason: str, *, evidence: Any = None) -> BusEvent:
        return self._finish(
            transaction_id,
            "aborted",
            {"reason": str(reason)[:4000], "evidence": evidence},
        )

    def events(self, transaction_id: str) -> list[BusEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM organism_events WHERE transaction_id=? ORDER BY seq ASC",
                (transaction_id,),
            ).fetchall()
        return [
            BusEvent(
                id=int(row["id"]),
                transaction_id=str(row["transaction_id"]),
                seq=int(row["seq"]),
                timestamp=str(row["timestamp"]),
                phase=str(row["phase"]),
                kind=str(row["kind"]),
                payload=json.loads(row["payload_json"]),
                prev_hash=str(row["prev_hash"]),
                event_hash=str(row["event_hash"]),
            )
            for row in rows
        ]

    def verify(self, transaction_id: str) -> tuple[bool, str | None]:
        previous_hash = "0" * 64
        expected_seq = 1
        for event in self.events(transaction_id):
            if event.seq != expected_seq:
                return False, f"non-contiguous event sequence at {event.seq}, expected {expected_seq}"
            payload_json = _canonical(event.payload)
            expected_hash = _event_hash(
                event.transaction_id,
                event.seq,
                event.timestamp,
                event.phase,
                event.kind,
                payload_json,
                previous_hash,
            )
            if event.prev_hash != previous_hash:
                return False, f"previous hash mismatch at event {event.id}"
            if event.event_hash != expected_hash:
                return False, f"event hash mismatch at event {event.id}"
            previous_hash = event.event_hash
            expected_seq += 1
        return True, None

    def incomplete(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM organism_transactions
                WHERE status='open' ORDER BY started_at ASC LIMIT ?
                """,
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def reconcile_incomplete(self, reason: str = "recovered after process interruption") -> int:
        count = 0
        for item in self.incomplete(1000):
            try:
                self.abort(str(item["transaction_id"]), reason)
            except RuntimeError:
                continue
            count += 1
        return count
