from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from .redaction import safe_tool_result, scrub_secret_text, scrub_secrets
from .sqlite_utils import inserted_row_id


MAX_STORED_PAYLOAD_BYTES = 512_000
FULL_PAYLOAD_WINDOW = 512
COMPACTION_INTERVAL = 64
COMPACTION_BATCH = 512
LEGACY_SENSITIVE_MIGRATION_BATCH = 4096
DATA_CLASSIFICATIONS = frozenset({"public", "internal", "sensitive", "secret"})
SENSITIVE_SOURCE_KINDS = frozenset({"body", "work_port", "resource_ingress"})
SENSITIVE_SOURCE_REFS = frozenset(
    {
        "read_workspace",
        "http_get",
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_fill",
        "mcp_discover",
        "mcp_call",
        "mcp_read_resource",
        "jsonrpc_call",
        "process_run",
        "submit_work",
        "check_work_outcome",
        "check_resource_ingress",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return {"type": "non_finite_number", "value": str(value)}
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
    data_classification: str = "internal"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ObservationStore:
    """Durable normalized sensorium with verified stored-payload reads.

    `payload_sha256` identifies the original canonical observation. A second
    `stored_payload_sha256` authenticates the representation currently retained in the
    SQLite row, including truncated previews and compacted markers. Compaction state is
    structural SQLite metadata (`is_compacted`), never inferred from JSON serialization
    during normal operation. Every authoritative read verifies the stored form; full
    retained payloads additionally rederive their original digest.
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
                    is_compacted INTEGER NOT NULL DEFAULT 0,
                    is_redacted INTEGER NOT NULL DEFAULT 0,
                    data_classification TEXT NOT NULL DEFAULT 'internal',
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
            if "is_compacted" not in columns:
                conn.execute(
                    "ALTER TABLE observations ADD COLUMN is_compacted INTEGER NOT NULL DEFAULT 0"
                )
                legacy_rows = conn.execute(
                    "SELECT id, payload_json FROM observations"
                ).fetchall()
                for row in legacy_rows:
                    try:
                        payload = json.loads(str(row["payload_json"]))
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict) and payload.get("_compacted") is True:
                        conn.execute(
                            "UPDATE observations SET is_compacted=1 WHERE id=?",
                            (int(row["id"]),),
                        )
            if "is_redacted" not in columns:
                conn.execute(
                    "ALTER TABLE observations ADD COLUMN is_redacted INTEGER NOT NULL DEFAULT 0"
                )
            if "data_classification" not in columns:
                conn.execute(
                    "ALTER TABLE observations ADD COLUMN data_classification TEXT NOT NULL DEFAULT 'internal'"
                )
            # Create the compaction index only after legacy schemas have gained the
            # structural column; otherwise opening an old database fails before migration.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_observations_compaction "
                "ON observations(is_compacted, id ASC)"
            )
            rows = conn.execute(
                "SELECT id, payload_json FROM observations WHERE stored_payload_sha256=''"
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE observations SET stored_payload_sha256=? WHERE id=?",
                    (_text_digest(str(row["payload_json"])), int(row["id"])),
                )
            self._migrate_legacy_sensitive_rows(
                conn,
                batch=LEGACY_SENSITIVE_MIGRATION_BATCH,
            )

    @staticmethod
    def _migrate_legacy_sensitive_rows(
        conn: sqlite3.Connection,
        *,
        batch: int,
    ) -> int:
        """Project legacy sensitive rows without changing their original digest.

        The operation is bounded and idempotent. Constructors migrate the most recent
        bounded window; operators may call ``migrate_legacy_sensitive_payloads`` again
        for unusually large historical databases.
        """

        batch = max(1, min(int(batch), LEGACY_SENSITIVE_MIGRATION_BATCH))
        kind_placeholders = ",".join("?" for _ in SENSITIVE_SOURCE_KINDS)
        ref_placeholders = ",".join("?" for _ in SENSITIVE_SOURCE_REFS)
        rows = conn.execute(
            f"""
            SELECT * FROM observations
            WHERE is_redacted=0 AND (
                source_kind IN ({kind_placeholders})
                OR source_ref IN ({ref_placeholders})
            )
            ORDER BY id DESC LIMIT ?
            """,
            (
                *sorted(SENSITIVE_SOURCE_KINDS),
                *sorted(SENSITIVE_SOURCE_REFS),
                batch,
            ),
        ).fetchall()
        if not rows:
            return 0
        conn.execute("PRAGMA secure_delete=ON")
        for row in rows:
            row_id = int(row["id"])
            raw_text = str(row["payload_json"])
            stored_digest = str(row["stored_payload_sha256"])
            if not stored_digest or _text_digest(raw_text) != stored_digest:
                raise RuntimeError(
                    f"legacy sensitive observation {row_id} stored payload digest mismatch"
                )
            try:
                payload = json.loads(raw_text)
                provenance = json.loads(str(row["provenance_json"]) or "{}")
            except (json.JSONDecodeError, TypeError) as exc:
                raise RuntimeError(
                    f"legacy sensitive observation {row_id} is malformed"
                ) from exc
            if not isinstance(provenance, dict):
                raise RuntimeError(
                    f"legacy sensitive observation {row_id} provenance is malformed"
                )
            original_digest = str(row["payload_sha256"])
            if isinstance(payload, dict) and (
                payload.get("_truncated") is True or payload.get("_compacted") is True
            ):
                if str(payload.get("original_sha256", "")) != original_digest:
                    raise RuntimeError(
                        f"legacy sensitive observation {row_id} original digest marker mismatch"
                    )
            elif _text_digest(_canonical_json(payload)) != original_digest:
                raise RuntimeError(
                    f"legacy sensitive observation {row_id} original payload digest mismatch"
                )
            projected, is_redacted = ObservationStore._persistence_projection(
                payload,
                classification="sensitive",
                original_digest=original_digest,
            )
            if not is_redacted:
                raise RuntimeError(
                    f"legacy sensitive observation {row_id} was not projected"
                )
            projected_json, _ = ObservationStore._bounded_payload(projected)
            safe_provenance = ObservationStore._persistence_provenance(
                provenance,
                classification="sensitive",
            )
            conn.execute(
                """
                UPDATE observations
                SET summary=?, payload_json=?, stored_payload_sha256=?,
                    is_redacted=1, data_classification='sensitive', provenance_json=?
                WHERE id=? AND is_redacted=0
                """,
                (
                    ObservationStore._persistence_summary(
                        row["summary"], classification="sensitive"
                    ),
                    projected_json,
                    _text_digest(projected_json),
                    _canonical_json(safe_provenance),
                    row_id,
                ),
            )
        return len(rows)

    def migrate_legacy_sensitive_payloads(
        self,
        *,
        batch: int = LEGACY_SENSITIVE_MIGRATION_BATCH,
    ) -> int:
        with self._connect() as conn:
            return self._migrate_legacy_sensitive_rows(conn, batch=batch)

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

    @staticmethod
    def _classification(source_kind: str, source_ref: str, requested: str | None) -> str:
        if requested is not None:
            classification = str(requested).strip().lower()
            if classification not in DATA_CLASSIFICATIONS:
                raise ValueError(
                    "data_classification must be public, internal, sensitive or secret"
                )
            return classification
        if source_kind in SENSITIVE_SOURCE_KINDS or source_ref in SENSITIVE_SOURCE_REFS:
            return "sensitive"
        return "internal"

    @staticmethod
    def _persistence_projection(
        payload: Any,
        *,
        classification: str,
        original_digest: str,
    ) -> tuple[Any, bool]:
        sanitized = scrub_secrets(payload)
        changed = _canonical_json(sanitized) != _canonical_json(payload)
        if classification == "secret":
            return (
                {
                    "_persistence_redacted": True,
                    "data_classification": classification,
                    "original_sha256": original_digest,
                    "projection": {"result_fingerprint": original_digest},
                },
                True,
            )
        if classification == "sensitive":
            projection = (
                safe_tool_result(sanitized)
                if isinstance(sanitized, dict)
                else {"result_fingerprint": original_digest, "type": type(payload).__name__}
            )
            return (
                {
                    "_persistence_redacted": True,
                    "data_classification": classification,
                    "original_sha256": original_digest,
                    "projection": projection,
                },
                True,
            )
        if changed:
            return (
                {
                    "_persistence_redacted": True,
                    "data_classification": classification,
                    "original_sha256": original_digest,
                    "projection": sanitized,
                },
                True,
            )
        return sanitized, False

    @staticmethod
    def _persistence_summary(value: Any, *, classification: str) -> str:
        text = str(value)
        if classification in {"sensitive", "secret"}:
            return (
                f"[{classification.upper()} OBSERVATION SUMMARY REDACTED] "
                f"sha256={_text_digest(text)}"
            )[:4000]
        return scrub_secret_text(text).strip()[:4000]

    @staticmethod
    def _persistence_provenance(
        value: Any,
        *,
        classification: str,
    ) -> dict[str, Any]:
        provenance = value if isinstance(value, dict) else {}
        if classification in {"sensitive", "secret"}:
            allowed = {
                "arguments_fingerprint",
                "authority",
                "capability",
                "mechanism",
                "provider",
                "source",
                "verifier",
            }
            result = {
                str(key): scrub_secrets(_jsonable(item))
                for key, item in provenance.items()
                if str(key).lower() in allowed
            }
            result["provenance_fingerprint"] = _text_digest(_canonical_json(provenance))
        else:
            projected = scrub_secrets(_jsonable(provenance))
            result = projected if isinstance(projected, dict) else {}
        result["data_classification"] = classification
        return result

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
                WHERE id < ? AND is_compacted=0
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
                    SET payload_json=?, stored_payload_sha256=?, is_compacted=1
                    WHERE id=? AND is_compacted=0
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
        data_classification: str | None = None,
    ) -> Observation:
        source_kind = scrub_secret_text(str(source_kind)).strip()[:64]
        source_ref = scrub_secret_text(str(source_ref)).strip()[:512]
        modality = str(modality).strip()[:64] or "structured"
        content_type = str(content_type).strip()[:128] or "application/octet-stream"
        if not source_kind or not source_ref:
            raise ValueError("source_kind and source_ref are required")
        trust = float(trust)
        if not math.isfinite(trust):
            raise ValueError("observation trust must be finite")
        trust = max(0.0, min(1.0, trust))
        classification = self._classification(
            source_kind, source_ref, data_classification
        )
        original_canonical = _canonical_json(payload)
        payload_digest = _text_digest(original_canonical)
        projected_payload, is_redacted = self._persistence_projection(
            payload,
            classification=classification,
            original_digest=payload_digest,
        )
        payload_json, _stored_projection_digest = self._bounded_payload(projected_payload)
        if is_redacted and len(payload_json.encode("utf-8")) > MAX_STORED_PAYLOAD_BYTES:
            # Defensive fallback; `_bounded_payload` normally already caps this value.
            payload_json = _canonical_json(
                {
                    "_persistence_redacted": True,
                    "data_classification": classification,
                    "original_sha256": payload_digest,
                    "projection": {"_truncated": True},
                }
            )
        elif is_redacted:
            bounded_item = json.loads(payload_json)
            if isinstance(bounded_item, dict) and bounded_item.get("_truncated") is True:
                payload_json = _canonical_json(
                    {
                        "_persistence_redacted": True,
                        "data_classification": classification,
                        "original_sha256": payload_digest,
                        "projection": {
                            "_truncated": True,
                            "stored_projection_sha256": _stored_projection_digest,
                        },
                    }
                )
        stored_digest = _text_digest(payload_json)
        safe_provenance = self._persistence_provenance(
            provenance or {},
            classification=classification,
        )
        provenance_json = _canonical_json(safe_provenance)
        timestamp = _now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO observations(
                    observed_at, transaction_id, source_kind, source_ref, modality,
                    content_type, trust, success, summary, payload_json,
                    payload_sha256, stored_payload_sha256, is_compacted, is_redacted,
                    data_classification, provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
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
                    self._persistence_summary(summary, classification=classification),
                    payload_json,
                    payload_digest,
                    stored_digest,
                    1 if is_redacted else 0,
                    classification,
                    provenance_json,
                ),
            )
            observation_id = inserted_row_id(cur, operation="record observation")
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
        is_compacted = bool(row["is_compacted"])
        if is_compacted:
            if not isinstance(payload, dict) or payload.get("_compacted") is not True:
                raise RuntimeError(
                    f"observation {int(row['id'])} structural compaction marker mismatch"
                )
            if str(payload.get("original_sha256", "")) != original_digest:
                raise RuntimeError(
                    f"observation {int(row['id'])} original digest marker mismatch"
                )
        elif bool(row["is_redacted"]):
            if not isinstance(payload, dict) or payload.get("_persistence_redacted") is not True:
                raise RuntimeError(
                    f"observation {int(row['id'])} structural redaction marker mismatch"
                )
            if str(payload.get("original_sha256", "")) != original_digest:
                raise RuntimeError(
                    f"observation {int(row['id'])} redacted original digest mismatch"
                )
        elif isinstance(payload, dict) and payload.get("_truncated") is True:
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
            data_classification=str(row["data_classification"]),
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
