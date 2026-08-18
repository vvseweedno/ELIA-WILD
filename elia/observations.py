from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any


MAX_STORED_PAYLOAD_BYTES = 512_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "sha256": sha256(value).hexdigest(), "size": len(value)}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "as_dict"):
        try:
            return _jsonable(value.as_dict())
        except Exception:
            pass
    return {"type": type(value).__name__, "repr": repr(value)[:8000]}


def _canonical_json(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class Observation:
    id: int
    observed_at: str
    transaction_id: str | None
    source_kind: str
    source_ref: str
    modality: str
    content_type: str
    trust: float
    success: bool
    summary: str
    payload: Any
    payload_sha256: str
    provenance: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ObservationStore:
    """Durable normalized sensorium shared by all external adapters.

    Observations are evidence, not authority. The store preserves provenance and a
    hash of the complete payload even when an oversized payload must be represented
    by a bounded preview in SQLite.
    """

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
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at TEXT NOT NULL,
                    transaction_id TEXT NULL,
                    source_kind TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    modality TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    trust REAL NOT NULL,
                    success INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    provenance_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_observations_time
                    ON observations(id DESC);
                CREATE INDEX IF NOT EXISTS idx_observations_source
                    ON observations(source_kind, source_ref, id DESC);
                CREATE INDEX IF NOT EXISTS idx_observations_transaction
                    ON observations(transaction_id, id ASC);
                """
            )

    @staticmethod
    def _bounded_payload(value: Any) -> tuple[str, str]:
        canonical = _canonical_json(value)
        digest = sha256(canonical.encode("utf-8")).hexdigest()
        raw = canonical.encode("utf-8")
        if len(raw) <= MAX_STORED_PAYLOAD_BYTES:
            return canonical, digest
        preview = raw[:MAX_STORED_PAYLOAD_BYTES].decode("utf-8", errors="replace")
        bounded = {
            "_truncated": True,
            "original_sha256": digest,
            "original_bytes": len(raw),
            "preview": preview,
        }
        return _canonical_json(bounded), digest

    def record(
        self,
        *,
        source_kind: str,
        source_ref: str,
        payload: Any,
        modality: str = "structured",
        content_type: str = "application/json",
        trust: float = 0.5,
        success: bool = True,
        summary: str = "",
        provenance: dict[str, Any] | None = None,
        transaction_id: str | None = None,
    ) -> Observation:
        source_kind = str(source_kind).strip()[:64]
        source_ref = str(source_ref).strip()[:512]
        modality = str(modality).strip()[:64] or "structured"
        content_type = str(content_type).strip()[:128] or "application/octet-stream"
        if not source_kind or not source_ref:
            raise ValueError("source_kind and source_ref are required")
        trust = max(0.0, min(1.0, float(trust)))
        payload_json, payload_digest = self._bounded_payload(payload)
        provenance_json = _canonical_json(provenance or {})
        timestamp = _now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO observations(
                    observed_at, transaction_id, source_kind, source_ref, modality,
                    content_type, trust, success, summary, payload_json,
                    payload_sha256, provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    str(transaction_id)[:128] if transaction_id else None,
                    source_kind,
                    source_ref,
                    modality,
                    content_type,
                    trust,
                    1 if success else 0,
                    str(summary).strip()[:4000],
                    payload_json,
                    payload_digest,
                    provenance_json,
                ),
            )
            observation_id = int(cur.lastrowid)
        observation = self.get(observation_id)
        if observation is None:
            raise RuntimeError("observation disappeared after insert")
        return observation

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Observation:
        return Observation(
            id=int(row["id"]),
            observed_at=str(row["observed_at"]),
            transaction_id=str(row["transaction_id"]) if row["transaction_id"] else None,
            source_kind=str(row["source_kind"]),
            source_ref=str(row["source_ref"]),
            modality=str(row["modality"]),
            content_type=str(row["content_type"]),
            trust=float(row["trust"]),
            success=bool(row["success"]),
            summary=str(row["summary"]),
            payload=json.loads(row["payload_json"]),
            payload_sha256=str(row["payload_sha256"]),
            provenance=json.loads(row["provenance_json"]),
        )

    def get(self, observation_id: int) -> Observation | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM observations WHERE id=?", (int(observation_id),)).fetchone()
        return self._from_row(row) if row else None

    def recent(self, limit: int = 32) -> list[Observation]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM observations ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 512)),),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def snapshot(self, limit: int = 12) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self.recent(limit)]
