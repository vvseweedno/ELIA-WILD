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
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


EXTERNAL_EFFECT_ACTIONS = frozenset(
    {
        "browser_click",
        "browser_fill",
        "process_run",
        "mcp_call",
        "jsonrpc_call",
        "submit_work",
    }
)

UNRESOLVED_STATUSES = frozenset({"prepared", "sending", "indeterminate"})
TERMINAL_STATUSES = frozenset(
    {"succeeded", "reconciled_effect", "reconciled_no_effect", "cancelled"}
)


class ExternalEffectError(RuntimeError):
    pass


class ExternalEffectIndeterminate(ExternalEffectError):
    def __init__(self, record: "ExternalEffectRecord") -> None:
        self.record = record
        super().__init__(
            "external effect is indeterminate and must be reconciled before retry: "
            f"{record.effect_id} {record.action_name}"
        )


@dataclass(frozen=True, slots=True)
class ExternalEffectRecord:
    id: int
    effect_id: str
    action_name: str
    arguments_sha256: str
    idempotency_key: str
    risk_class: str
    status: str
    prepared_at: str
    updated_at: str
    result_sha256: str
    evidence: str
    error: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExternalEffectLedger:
    """Durable intent ledger shared by every externally side-effecting actuator.

    The ledger does not pretend that arbitrary browser/process/MCP providers support
    exactly-once semantics. Instead it guarantees the local half of the protocol:

    * intent is durable before the external call;
    * the call is marked ``sending`` before control crosses the local boundary;
    * a process death while sending becomes ``indeterminate`` on recovery;
    * an unresolved matching intent blocks blind retries;
    * reconciliation is explicit and evidence-bearing.

    Adapters with native idempotency may additionally use ``idempotency_key`` when their
    remote protocol supports it. The generic ledger never injects a key into an unknown
    protocol because doing so could itself change remote semantics.
    """

    def __init__(self, path: Path) -> None:
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
                CREATE TABLE IF NOT EXISTS external_effect_intents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    effect_id TEXT NOT NULL UNIQUE,
                    action_name TEXT NOT NULL,
                    arguments_sha256 TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    risk_class TEXT NOT NULL,
                    status TEXT NOT NULL,
                    prepared_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    result_sha256 TEXT NOT NULL DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_external_effect_status
                    ON external_effect_intents(status, updated_at, id);
                CREATE INDEX IF NOT EXISTS idx_external_effect_fingerprint
                    ON external_effect_intents(action_name, arguments_sha256, id DESC);
                """
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ExternalEffectRecord:
        return ExternalEffectRecord(
            id=int(row["id"]),
            effect_id=str(row["effect_id"]),
            action_name=str(row["action_name"]),
            arguments_sha256=str(row["arguments_sha256"]),
            idempotency_key=str(row["idempotency_key"]),
            risk_class=str(row["risk_class"]),
            status=str(row["status"]),
            prepared_at=str(row["prepared_at"]),
            updated_at=str(row["updated_at"]),
            result_sha256=str(row["result_sha256"]),
            evidence=str(row["evidence"]),
            error=str(row["error"]),
        )

    def get(self, effect_id: str) -> ExternalEffectRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM external_effect_intents WHERE effect_id=?",
                (str(effect_id),),
            ).fetchone()
        return self._from_row(row) if row else None

    def unresolved_for(self, action_name: str, arguments: dict[str, Any]) -> ExternalEffectRecord | None:
        args_sha = _digest(arguments)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM external_effect_intents
                WHERE action_name=? AND arguments_sha256=?
                  AND status IN ('prepared','sending','indeterminate')
                ORDER BY id DESC LIMIT 1
                """,
                (str(action_name), args_sha),
            ).fetchone()
        return self._from_row(row) if row else None

    def prepare(
        self,
        action_name: str,
        arguments: dict[str, Any],
        *,
        risk_class: str = "external_side_effect",
    ) -> ExternalEffectRecord:
        name = str(action_name).strip()[:128]
        if name not in EXTERNAL_EFFECT_ACTIONS:
            raise ValueError(f"action is not classified as an external effect: {name!r}")
        args_sha = _digest(arguments)
        timestamp = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM external_effect_intents
                WHERE action_name=? AND arguments_sha256=?
                  AND status IN ('prepared','sending','indeterminate')
                ORDER BY id DESC LIMIT 1
                """,
                (name, args_sha),
            ).fetchone()
            if row is not None:
                current = self._from_row(row)
                if current.status in {"sending", "indeterminate"}:
                    raise ExternalEffectIndeterminate(current)
                return current

            effect_id = uuid4().hex
            idempotency_key = f"elia-effect-{effect_id}"
            cur = conn.execute(
                """
                INSERT INTO external_effect_intents(
                    effect_id, action_name, arguments_sha256, idempotency_key,
                    risk_class, status, prepared_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'prepared', ?, ?)
                """,
                (
                    effect_id,
                    name,
                    args_sha,
                    str(risk_class)[:128],
                    timestamp,
                    timestamp,
                ),
            )
            row = conn.execute(
                "SELECT * FROM external_effect_intents WHERE id=?",
                (int(cur.lastrowid),),
            ).fetchone()
        if row is None:
            raise ExternalEffectError("external effect intent disappeared after prepare")
        return self._from_row(row)

    def mark_sending(self, effect_id: str) -> ExternalEffectRecord:
        timestamp = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                UPDATE external_effect_intents
                SET status='sending', updated_at=?, error=''
                WHERE effect_id=? AND status='prepared'
                """,
                (timestamp, str(effect_id)),
            )
            row = conn.execute(
                "SELECT * FROM external_effect_intents WHERE effect_id=?",
                (str(effect_id),),
            ).fetchone()
            if row is None:
                raise ExternalEffectError(f"unknown external effect: {effect_id}")
            record = self._from_row(row)
            if cur.rowcount != 1 and record.status != "sending":
                raise ExternalEffectError(
                    f"cannot send external effect {effect_id} from status {record.status!r}"
                )
        return record

    def record_result(
        self,
        effect_id: str,
        *,
        ok: bool,
        result: Any,
        no_effect_proven: bool = False,
    ) -> ExternalEffectRecord:
        timestamp = _now()
        result_sha = _digest(result)
        status = "succeeded" if ok else ("reconciled_no_effect" if no_effect_proven else "indeterminate")
        error = "" if ok or no_effect_proven else "external call failed without proof that no remote effect occurred"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                UPDATE external_effect_intents
                SET status=?, updated_at=?, result_sha256=?, error=?
                WHERE effect_id=? AND status IN ('sending','prepared')
                """,
                (status, timestamp, result_sha, error, str(effect_id)),
            )
            row = conn.execute(
                "SELECT * FROM external_effect_intents WHERE effect_id=?",
                (str(effect_id),),
            ).fetchone()
            if row is None:
                raise ExternalEffectError(f"unknown external effect: {effect_id}")
            record = self._from_row(row)
            if cur.rowcount != 1 and record.status != status:
                raise ExternalEffectError(
                    f"cannot record result for external effect {effect_id} from {record.status!r}"
                )
        return record

    def mark_indeterminate(self, effect_id: str, error: str) -> ExternalEffectRecord:
        timestamp = _now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE external_effect_intents
                SET status='indeterminate', updated_at=?, error=?
                WHERE effect_id=? AND status IN ('prepared','sending')
                """,
                (timestamp, str(error)[:4000], str(effect_id)),
            )
            row = conn.execute(
                "SELECT * FROM external_effect_intents WHERE effect_id=?",
                (str(effect_id),),
            ).fetchone()
        if row is None:
            raise ExternalEffectError(f"unknown external effect: {effect_id}")
        return self._from_row(row)

    def recover_interrupted(self) -> list[ExternalEffectRecord]:
        """Turn process-killed in-flight sends into explicit ambiguity before cognition."""
        timestamp = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE external_effect_intents
                SET status='indeterminate', updated_at=?,
                    error='process ended while external effect was marked sending; reconcile remote state before retry'
                WHERE status='sending'
                """,
                (timestamp,),
            )
            rows = conn.execute(
                """
                SELECT * FROM external_effect_intents
                WHERE status='indeterminate'
                ORDER BY id ASC
                """
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def reconcile(
        self,
        effect_id: str,
        *,
        remote_effect_observed: bool,
        evidence: str,
    ) -> ExternalEffectRecord:
        evidence = str(evidence).strip()[:8000]
        if not evidence:
            raise ValueError("external effect reconciliation requires evidence")
        timestamp = _now()
        status = "reconciled_effect" if remote_effect_observed else "reconciled_no_effect"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                UPDATE external_effect_intents
                SET status=?, updated_at=?, evidence=?, error=''
                WHERE effect_id=? AND status='indeterminate'
                """,
                (status, timestamp, evidence, str(effect_id)),
            )
            row = conn.execute(
                "SELECT * FROM external_effect_intents WHERE effect_id=?",
                (str(effect_id),),
            ).fetchone()
            if row is None:
                raise ExternalEffectError(f"unknown external effect: {effect_id}")
            record = self._from_row(row)
            if cur.rowcount != 1 and record.status != status:
                raise ExternalEffectError(
                    f"external effect {effect_id} is not awaiting reconciliation"
                )
        return record

    def recent(self, limit: int = 64) -> list[ExternalEffectRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM external_effect_intents ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 512)),),
            ).fetchall()
        return [self._from_row(row) for row in reversed(rows)]

    def diagnostics(self) -> dict[str, Any]:
        recent = self.recent(64)
        unresolved = [item for item in recent if item.status in UNRESOLVED_STATUSES]
        return {
            "unresolved_count": len(unresolved),
            "unresolved": [item.as_dict() for item in unresolved[-16:]],
            "recent": [item.as_dict() for item in recent[-32:]],
            "rule": (
                "Every external side effect is durably prepared before send; unresolved/indeterminate "
                "effects block blind retries until evidence-bearing reconciliation."
            ),
        }
