from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any


MAX_STORED_PAYLOAD_BYTES = 512_000
FULL_PAYLOAD_WINDOW = 512
COMPACTION_INTERVAL = 64
COMPACTION_BATCH = 512


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
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _text_digest(value: str) -> str:
    return sha256(str(value).encode("utf-8")).hexdigest()


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
    """Durable normalized sensorium with verified stored-payload reads.

    `payload_sha256` identifies the original canonical observation. A second
    `stored_payload_sha256` authenticates the representation currently retained in the
    SQLite row, including truncated previews and compacted markers. Every authoritative
    read verifies the stored form; full retained payloads additionally rederive their
    original digest. Compacted/truncated rows must carry the exact original digest.
    """

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
                    stored_payload_sha256 TEXT NOT NULL DEFAULT '',
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
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(observations)").fetchall()
            }
            if "stored_payload_sha256" not in columns:
                conn.execute(
                    "ALTER TABLE observations ADD COLUMN stored_payload_sha256 TEXT NOT NULL DEFAULT ''"
                )
            rows = conn.execute(
                "SELECT id, payload_json FROM observations WHERE stored_payload_sha256=''"
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE observations SET stored_payload_sha256=? WHERE id=?",
                    (_text_digest(str(row["payload_json"])), int(row["id"])),
                )

    @staticmethod
    def _bounded_payload(value: Any) -> tuple[str, str]:
        canonical = _canonical_json(value)
        digest = _text_digest(canonical)
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

    def compact_aged_payloads(
        self,
        *,
        keep_recent: int = FULL_PAYLOAD_WINDOW,
        batch: int = COMPACTION_BATCH,
    ) -> int:
        keep_recent = max(1, int(keep_recent))
        batch = max(1, min(int(batch), 4096))
        with self._connect() as conn:
            cutoff_row = conn.execute(
                "SELECT id FROM observations ORDER BY id DESC LIMIT 1 OFFSET ?",
                (keep_recent - 1,),
            ).fetchone()
            if cutoff_row is None:
                return 0
            cutoff = int(cutoff_row["id"])
            rows = conn.execute(
                """
                SELECT id, payload_json, payload_sha256, stored_payload_sha256
                FROM observations
                WHERE id < ? AND payload_json NOT LIKE '%\"_compacted\":true%'
                ORDER BY id ASC LIMIT ?
                """,
                (cutoff, batch),
            ).fetchall()
            for row in rows:
                raw_text = str(row["payload_json"])
                if _text_digest(raw_text) != str(row["stored_payload_sha256"]):
                    raise RuntimeError(
                        f"observation {int(row['id'])} stored payload digest mismatch before compaction"
                    )
                compacted = _canonical_json(
                    {
                        "_compacted": True,
                        "original_sha256": str(row["payload_sha256"]),
                        "previous_stored_bytes": len(raw_text.encode("utf-8")),
                    }
                )
                conn.execute(
                    """
                    UPDATE observations
                    SET payload_json=?, stored_payload_sha256=?
                    WHERE id=?
                    """,
                    (compacted, _text_digest(compacted), int(row["id"])),
                )
            return len(rows)

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
        stored_digest = _text_digest(payload_json)
        provenance_json = _canonical_json(provenance or {})
        timestamp = _now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO observations(
                    observed_at, transaction_id, source_kind, source_ref, modality,
                    content_type, trust, success, summary, payload_json,
                    payload_sha256, stored_payload_sha256, provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    stored_digest,
                    provenance_json,
                ),
            )
            observation_id = int(cur.lastrowid)
        if observation_id % COMPACTION_INTERVAL == 0:
            self.compact_aged_payloads()
        observation = self.get(observation_id)
        if observation is None:
            raise RuntimeError("observation disappeared after insert")
        return observation

    @staticmethod
    def _verified_payload(row: sqlite3.Row) -> Any:
        raw = str(row["payload_json"])
        stored_digest = str(row["stored_payload_sha256"])
        actual_stored = _text_digest(raw)
        if actual_stored != stored_digest:
            raise RuntimeError(
                f"observation {int(row['id'])} stored payload digest mismatch"
            )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"observation {int(row['id'])} payload JSON is malformed"
            ) from exc
        original_digest = str(row["payload_sha256"])
        if isinstance(payload, dict) and (
            payload.get("_truncated") is True or payload.get("_compacted") is True
        ):
            if str(payload.get("original_sha256", "")) != original_digest:
                raise RuntimeError(
                    f"observation {int(row['id'])} original digest marker mismatch"
                )
        else:
            actual_original = _text_digest(_canonical_json(payload))
            if actual_original != original_digest:
                raise RuntimeError(
                    f"observation {int(row['id'])} original payload digest mismatch"
                )
        return payload

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> Observation:
        return Observation(
            id=int(row["id"]),
            observed_at=str(row["observed_at"]),
            transaction_id=(
                str(row["transaction_id"]) if row["transaction_id"] else None
            ),
            source_kind=str(row["source_kind"]),
            source_ref=str(row["source_ref"]),
            modality=str(row["modality"]),
            content_type=str(row["content_type"]),
            trust=float(row["trust"]),
            success=bool(row["success"]),
            summary=str(row["summary"]),
            payload=cls._verified_payload(row),
            payload_sha256=str(row["payload_sha256"]),
            provenance=json.loads(row["provenance_json"]),
        )

    def get(self, observation_id: int) -> Observation | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM observations WHERE id=?", (int(observation_id),)
            ).fetchone()
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
