from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from .body.mcp import MCPBody
from .body.types import bounded_json_value
from .causal import CausalMemoryStore
from .observations import ObservationStore
from .resource_ecology import ResourceEcologyStore, WorkItem
from .sqlite_utils import inserted_row_id
from .state_bus import OrganismStateBus
from .tools import Capability, ToolResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: Any) -> str:
    bounded = bounded_json_value(
        value,
        field="work-port fingerprint value",
        max_bytes=512_000,
        max_depth=12,
        max_items=4096,
    )
    payload = json.dumps(
        bounded,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class WorkPortIntent:
    id: int
    work_item_id: int
    port_name: str
    idempotency_key: str
    artifact_sha256: str
    created_at: str
    updated_at: str
    status: str
    attempt_count: int
    last_error: str
    submission_ref: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WorkPortSubmission:
    id: int
    work_item_id: int
    port_name: str
    submitted_at: str
    updated_at: str
    submission_observation_id: int
    submission_ref: str
    remote_status: str
    last_outcome_observation_id: int | None
    response_fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkPortStore:
    """Durable exactly-once intent/outcome bridge for configured external ports.

    `work_port_intents` is the local outbox. Intent is persisted before any remote
    side effect. `sending` surviving a process restart is treated as ambiguous and
    must be reconciled by remote lookup; ordinary submission never blindly retries it.
    """

    INTENT_STATUSES = {"prepared", "sending", "submitted", "indeterminate"}

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
                CREATE TABLE IF NOT EXISTS work_port_intents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    work_item_id INTEGER NOT NULL UNIQUE,
                    port_name TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    artifact_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'prepared',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    submission_ref TEXT NULL,
                    FOREIGN KEY(work_item_id) REFERENCES ecology_work_items(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_work_port_intent_status
                    ON work_port_intents(status, updated_at, id);

                CREATE TABLE IF NOT EXISTS work_port_submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    work_item_id INTEGER NOT NULL UNIQUE,
                    port_name TEXT NOT NULL,
                    submitted_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    submission_observation_id INTEGER NOT NULL,
                    submission_ref TEXT NOT NULL,
                    remote_status TEXT NOT NULL DEFAULT 'submitted',
                    last_outcome_observation_id INTEGER NULL,
                    response_fingerprint TEXT NOT NULL,
                    FOREIGN KEY(work_item_id) REFERENCES ecology_work_items(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_work_port_status
                    ON work_port_submissions(remote_status, updated_at, id);
                """
            )

    @staticmethod
    def _intent_from_row(row: sqlite3.Row) -> WorkPortIntent:
        return WorkPortIntent(
            id=int(row["id"]),
            work_item_id=int(row["work_item_id"]),
            port_name=str(row["port_name"]),
            idempotency_key=str(row["idempotency_key"]),
            artifact_sha256=str(row["artifact_sha256"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            status=str(row["status"]),
            attempt_count=int(row["attempt_count"]),
            last_error=str(row["last_error"]),
            submission_ref=(str(row["submission_ref"]) if row["submission_ref"] else None),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> WorkPortSubmission:
        return WorkPortSubmission(
            id=int(row["id"]),
            work_item_id=int(row["work_item_id"]),
            port_name=str(row["port_name"]),
            submitted_at=str(row["submitted_at"]),
            updated_at=str(row["updated_at"]),
            submission_observation_id=int(row["submission_observation_id"]),
            submission_ref=str(row["submission_ref"]),
            remote_status=str(row["remote_status"]),
            last_outcome_observation_id=(
                int(row["last_outcome_observation_id"])
                if row["last_outcome_observation_id"] is not None
                else None
            ),
            response_fingerprint=str(row["response_fingerprint"]),
        )

    def intent_for_work(self, work_item_id: int) -> WorkPortIntent | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM work_port_intents WHERE work_item_id=?",
                (int(work_item_id),),
            ).fetchone()
        return self._intent_from_row(row) if row else None

    def prepare_intent(
        self,
        *,
        work_item_id: int,
        port_name: str,
        idempotency_key: str,
        artifact_sha256: str,
    ) -> WorkPortIntent:
        timestamp = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM work_port_intents WHERE work_item_id=?",
                (int(work_item_id),),
            ).fetchone()
            if row is not None:
                current = self._intent_from_row(row)
                if (
                    current.port_name != str(port_name)
                    or current.idempotency_key != str(idempotency_key)
                    or current.artifact_sha256 != str(artifact_sha256)
                ):
                    raise PermissionError(
                        "work item already has a different immutable external submission intent"
                    )
                return current
            cur = conn.execute(
                """
                INSERT INTO work_port_intents(
                    work_item_id, port_name, idempotency_key, artifact_sha256,
                    created_at, updated_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'prepared')
                """,
                (
                    int(work_item_id),
                    str(port_name)[:128],
                    str(idempotency_key)[:128],
                    str(artifact_sha256)[:128],
                    timestamp,
                    timestamp,
                ),
            )
            intent_id = inserted_row_id(cur, operation="work-port intent insert")
            row = conn.execute(
                "SELECT * FROM work_port_intents WHERE id=?", (intent_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("work-port intent disappeared after prepare")
        return self._intent_from_row(row)

    def mark_sending(self, work_item_id: int) -> WorkPortIntent:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                UPDATE work_port_intents
                SET status='sending', updated_at=?, attempt_count=attempt_count+1, last_error=''
                WHERE work_item_id=? AND status='prepared'
                """,
                (_now(), int(work_item_id)),
            )
            if cur.rowcount != 1:
                row = conn.execute(
                    "SELECT status FROM work_port_intents WHERE work_item_id=?",
                    (int(work_item_id),),
                ).fetchone()
                status = str(row["status"]) if row else "missing"
                raise RuntimeError(f"submission intent cannot send from state {status!r}")
        intent = self.intent_for_work(work_item_id)
        if intent is None:
            raise RuntimeError("work-port intent disappeared before send")
        return intent

    def mark_indeterminate(self, work_item_id: int, error: str) -> WorkPortIntent:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE work_port_intents
                SET status='indeterminate', updated_at=?, last_error=?
                WHERE work_item_id=? AND status IN ('sending','indeterminate')
                """,
                (_now(), str(error)[:4000], int(work_item_id)),
            )
        intent = self.intent_for_work(work_item_id)
        if intent is None:
            raise RuntimeError("work-port intent disappeared while marking indeterminate")
        return intent

    def mark_not_found_after_lookup(self, work_item_id: int) -> WorkPortIntent:
        """Authoritative remote lookup proved no submission exists; permit one new send."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                UPDATE work_port_intents
                SET status='prepared', updated_at=?, last_error=''
                WHERE work_item_id=? AND status IN ('sending','indeterminate')
                """,
                (_now(), int(work_item_id)),
            )
            if cur.rowcount != 1:
                raise RuntimeError("only ambiguous intents may be reset after authoritative lookup")
        intent = self.intent_for_work(work_item_id)
        if intent is None:
            raise RuntimeError("work-port intent disappeared after lookup reset")
        return intent

    def submission_for_work(self, work_item_id: int) -> WorkPortSubmission | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM work_port_submissions WHERE work_item_id=?",
                (int(work_item_id),),
            ).fetchone()
        return self._from_row(row) if row else None

    def finalize_submission(
        self,
        *,
        work_item_id: int,
        port_name: str,
        observation_id: int,
        submission_ref: str,
        response: dict[str, Any],
    ) -> WorkPortSubmission:
        """Atomically bind remote success to the prepared local intent/submission row."""
        submission_ref = str(submission_ref).strip()[:2000]
        if not submission_ref:
            raise ValueError("submission_ref is required")
        timestamp = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM work_port_submissions WHERE work_item_id=?",
                (int(work_item_id),),
            ).fetchone()
            if existing is not None:
                return self._from_row(existing)
            intent = conn.execute(
                "SELECT * FROM work_port_intents WHERE work_item_id=?",
                (int(work_item_id),),
            ).fetchone()
            if intent is None or str(intent["status"]) not in {"sending", "indeterminate"}:
                raise RuntimeError("submission finalization requires a sending/indeterminate intent")
            cur = conn.execute(
                """
                INSERT INTO work_port_submissions(
                    work_item_id, port_name, submitted_at, updated_at,
                    submission_observation_id, submission_ref, remote_status,
                    response_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?)
                """,
                (
                    int(work_item_id),
                    str(port_name)[:128],
                    timestamp,
                    timestamp,
                    int(observation_id),
                    submission_ref,
                    _fingerprint(response),
                ),
            )
            submission_id = inserted_row_id(cur, operation="work-port submission insert")
            conn.execute(
                """
                UPDATE work_port_intents
                SET status='submitted', updated_at=?, last_error='', submission_ref=?
                WHERE work_item_id=?
                """,
                (timestamp, submission_ref, int(work_item_id)),
            )
            row = conn.execute(
                "SELECT * FROM work_port_submissions WHERE id=?", (submission_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("work-port submission disappeared after insert")
        return self._from_row(row)

    # Backward-compatible name for callers/tests created before the intent layer.
    record_submission = finalize_submission

    def record_outcome(
        self,
        *,
        work_item_id: int,
        observation_id: int,
        status: str,
        response: dict[str, Any],
    ) -> WorkPortSubmission:
        status = str(status).strip().lower()
        if status not in {"pending", "accepted", "rejected"}:
            raise ValueError(f"unsupported external work status: {status}")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM work_port_submissions WHERE work_item_id=?",
                (int(work_item_id),),
            ).fetchone()
            if row is None:
                raise ValueError(f"work item has no external submission: {work_item_id}")
            conn.execute(
                """
                UPDATE work_port_submissions
                SET updated_at=?, remote_status=?, last_outcome_observation_id=?,
                    response_fingerprint=?
                WHERE work_item_id=?
                """,
                (
                    _now(),
                    status,
                    int(observation_id),
                    _fingerprint(response),
                    int(work_item_id),
                ),
            )
            updated = conn.execute(
                "SELECT * FROM work_port_submissions WHERE work_item_id=?",
                (int(work_item_id),),
            ).fetchone()
        if updated is None:
            raise RuntimeError("work-port submission disappeared after outcome update")
        return self._from_row(updated)

    def active(self, limit: int = 64) -> list[WorkPortSubmission]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM work_port_submissions
                WHERE remote_status IN ('submitted','pending','accepted')
                ORDER BY updated_at ASC, id ASC LIMIT ?
                """,
                (max(1, min(int(limit), 512)),),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def ambiguous_intents(self, limit: int = 64) -> list[WorkPortIntent]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM work_port_intents
                WHERE status IN ('sending','indeterminate')
                ORDER BY updated_at ASC, id ASC LIMIT ?
                """,
                (max(1, min(int(limit), 512)),),
            ).fetchall()
        return [self._intent_from_row(row) for row in rows]


class WorkPortRegistry:
    """Authorized external-work ports over fixed MCP server/tool bindings.

    Every side-effecting submission is guarded by a durable local intent and a stable
    idempotency key. Port configuration must explicitly promise remote idempotency and
    provide a lookup tool so ambiguous process/network outcomes can be reconciled
    without blind resubmission.
    """

    MAX_ARTIFACT_BYTES = 512_000

    def __init__(
        self,
        workspace: Path,
        tool_config: dict[str, Any] | None = None,
        *,
        mcp_target_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.config = dict((tool_config or {}).get("work_ports") or {})
        database = self.workspace.parent / "memory.sqlite3"
        self.observations = ObservationStore(database)
        self.causal = CausalMemoryStore(database)
        self.state_bus = OrganismStateBus(database)
        self.resource_ecology = ResourceEcologyStore(database)
        self.store = WorkPortStore(database)
        body = dict((tool_config or {}).get("body") or {})
        self.mcp = MCPBody(
            dict(body.get("mcp") or {}),
            target_overrides=mcp_target_overrides,
        )
        self._recover_interrupted_intents()
        self._reconcile_local_projection()

    def _recover_interrupted_intents(self) -> None:
        # A previous process may have died anywhere after marking `sending`. We cannot
        # know whether the remote side effect happened, so convert it to indeterminate.
        for intent in self.store.ambiguous_intents(512):
            if intent.status == "sending":
                self.store.mark_indeterminate(
                    intent.work_item_id,
                    "process restarted while remote submission outcome was unknown",
                )

    def _reconcile_local_projection(self) -> None:
        # If remote success and WorkPortStore committed but ResourceEcology did not,
        # deterministically complete the local projection without another remote send.
        for submission in self.store.active(512):
            work = self.resource_ecology.work_item(submission.work_item_id)
            if work is not None and work.status == "staged":
                try:
                    self.resource_ecology.record_submission(
                        work_item_id=work.id,
                        observation_id=submission.submission_observation_id,
                        evidence=(
                            "Recovered local Resource Ecology projection from durable "
                            f"work-port submission {submission.id}."
                        ),
                    )
                except ValueError:
                    pass

    def ports(self) -> dict[str, dict[str, Any]]:
        raw = self.config.get("ports") or {}
        if not isinstance(raw, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for name, item in raw.items():
            if not isinstance(item, dict) or not bool(item.get("enabled", True)):
                continue
            port_name = str(name).strip()[:128]
            if port_name:
                result[port_name] = dict(item)
        return result

    @staticmethod
    def _port_contract_ready(item: dict[str, Any]) -> bool:
        return bool(item.get("supports_idempotency", False)) and bool(
            str(item.get("lookup_tool", "")).strip()
        )

    @property
    def enabled(self) -> bool:
        return (
            bool(self.config.get("enabled", False))
            and bool(self.ports())
            and self.mcp.enabled
            and all(self._port_contract_ready(item) for item in self.ports().values())
        )

    def _readiness(self) -> str:
        if not bool(self.config.get("enabled", False)):
            return "disabled"
        if not self.ports():
            return "no_configured_ports"
        if not all(self._port_contract_ready(item) for item in self.ports().values()):
            return "idempotency_and_lookup_contract_required"
        if not self.mcp.installed:
            return "mcp_v2_not_installed"
        if not self.mcp.enabled:
            return "configured_mcp_body_unavailable"
        return "ready"

    def catalog(self) -> dict[str, dict[str, Any]]:
        readiness = self._readiness()
        enabled = self.enabled
        return {
            "submit_work": Capability(
                "submit_work",
                "Submit one staged work item through one preconfigured idempotent external work port.",
                "{port: configured_name, work_item_id: int}",
                "configured_external_submission",
                "may create one externally deduplicated submission through the fixed port binding",
                "configured_work_port",
                "network",
                enabled=enabled,
                readiness=readiness,
            ).as_dict(),
            "check_work_outcome": Capability(
                "check_work_outcome",
                "Poll the configured external work port for one confirmed submission.",
                "{work_item_id: int}",
                "configured_external_outcome_read",
                "reads remote submission status; accepted/rejected may update local work lifecycle",
                "configured_work_port",
                "network",
                enabled=enabled,
                readiness=readiness,
            ).as_dict(),
            "reconcile_work_submission": Capability(
                "reconcile_work_submission",
                "Resolve one ambiguous external submission by stable idempotency key without resending it.",
                "{work_item_id: int}",
                "configured_external_reconciliation",
                "read-only remote lookup may repair local submission state",
                "configured_work_port",
                "network",
                enabled=enabled,
                readiness=readiness,
            ).as_dict(),
        }

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "readiness": self._readiness(),
            "ports": {
                name: {
                    "server": str(item.get("server", "")),
                    "submit_tool": str(item.get("submit_tool", "")),
                    "outcome_tool": str(item.get("outcome_tool", "")),
                    "lookup_tool": str(item.get("lookup_tool", "")),
                    "supports_idempotency": bool(item.get("supports_idempotency", False)),
                }
                for name, item in self.ports().items()
            },
            "active_submissions": [item.as_dict() for item in self.store.active(32)],
            "ambiguous_intents": [
                item.as_dict() for item in self.store.ambiguous_intents(32)
            ],
        }

    def _port(self, name: str) -> dict[str, Any]:
        port = self.ports().get(str(name))
        if port is None:
            raise ValueError(f"unknown or disabled work port: {name!r}")
        for field in ("server", "submit_tool", "outcome_tool", "lookup_tool"):
            if not str(port.get(field, "")).strip():
                raise ValueError(f"work port {name!r} has no {field}")
        if not bool(port.get("supports_idempotency", False)):
            raise PermissionError(
                f"work port {name!r} has no explicit remote idempotency contract"
            )
        return port

    def _artifact(self, work: WorkItem, port: dict[str, Any]) -> dict[str, Any]:
        if work.status != "staged" or not work.artifact_path:
            raise ValueError("only a staged work item with an artifact may be submitted")
        path = (self.workspace / work.artifact_path).resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError("staged artifact path escapes workspace")
        if not path.is_file():
            raise ValueError("staged artifact does not exist")
        max_bytes = max(
            1,
            min(
                int(port.get("max_artifact_bytes", self.MAX_ARTIFACT_BYTES)),
                self.MAX_ARTIFACT_BYTES,
            ),
        )
        payload = path.read_bytes()
        if len(payload) > max_bytes:
            raise ValueError(
                f"staged artifact exceeds work-port limit: {len(payload)} > {max_bytes}"
            )
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Genesis work ports currently accept UTF-8 artifacts only") from exc
        return {
            "name": path.name,
            "relative_path": work.artifact_path,
            "sha256": sha256(payload).hexdigest(),
            "bytes": len(payload),
            "text": text,
        }

    @staticmethod
    def _structured(result: ToolResult) -> dict[str, Any]:
        if not result.ok or not isinstance(result.data, dict):
            raise RuntimeError(result.error or "work-port MCP call failed")
        structured = result.data.get("structured_content")
        if not isinstance(structured, dict):
            raise ValueError("work-port MCP tool must return structured_content object")
        return dict(structured)

    @staticmethod
    def _idempotency_key(work: WorkItem, port_name: str, artifact: dict[str, Any]) -> str:
        return _fingerprint(
            {
                "type": "elia_external_work_submission_v1",
                "work_item_id": work.id,
                "opportunity_id": work.opportunity_id,
                "port": str(port_name),
                "artifact_sha256": artifact["sha256"],
            }
        )

    def _record_observation(
        self,
        *,
        capability: str,
        arguments: dict[str, Any],
        result: ToolResult,
        transaction_id: str,
        duration_ms: float,
    ) -> int:
        observation = self.observations.record(
            source_kind="work_port",
            source_ref=capability,
            payload=result.as_dict(),
            trust=0.8,
            success=result.ok,
            summary=(result.error or f"{capability} completed")[:4000],
            provenance={
                "capability": capability,
                "arguments_fingerprint": _fingerprint(arguments),
                "authority": "configured_work_port",
            },
            transaction_id=transaction_id,
        )
        experience = self.causal.record_intervention(
            action_name=capability,
            arguments=arguments,
            outcome=result.as_dict(),
            success=result.ok,
            duration_ms=duration_ms,
            observation_id=observation.id,
            transaction_id=transaction_id,
            source="work_port_registry",
            outcome_summary=result.error or f"{capability} ok={result.ok}",
        )
        self.state_bus.append(
            transaction_id,
            phase="observation",
            kind="WORK_PORT_OBSERVATION",
            payload={
                "capability": capability,
                "observation_id": observation.id,
                "payload_sha256": observation.payload_sha256,
                "experience_id": experience.id,
                "success": result.ok,
            },
        )
        return observation.id

    def submit(self, port_name: str, work_item_id: int) -> ToolResult:
        capability = "submit_work"
        args = {"port": str(port_name), "work_item_id": int(work_item_id)}
        transaction_id = self.state_bus.begin("work_port:submit")
        self.state_bus.append(
            transaction_id,
            phase="action",
            kind="WORK_PORT_ATTEMPT",
            payload={"capability": capability, "arguments_fingerprint": _fingerprint(args)},
        )
        started = time.monotonic()
        sending_started = False
        try:
            if not self.enabled:
                raise RuntimeError(f"work ports unavailable: {self._readiness()}")
            if self.store.submission_for_work(int(work_item_id)) is not None:
                raise ValueError("work item already has an external submission")
            work = self.resource_ecology.work_item(int(work_item_id))
            if work is None:
                raise ValueError(f"work item does not exist: {work_item_id}")
            port = self._port(port_name)
            artifact = self._artifact(work, port)
            idempotency_key = self._idempotency_key(work, port_name, artifact)
            intent = self.store.prepare_intent(
                work_item_id=work.id,
                port_name=str(port_name),
                idempotency_key=idempotency_key,
                artifact_sha256=str(artifact["sha256"]),
            )
            if intent.status in {"sending", "indeterminate"}:
                if intent.status == "sending":
                    intent = self.store.mark_indeterminate(
                        work.id,
                        "prior sending state has ambiguous remote outcome; reconcile before retry",
                    )
                raise RuntimeError(
                    "external submission outcome is indeterminate; use reconcile_work_submission before any retry"
                )
            if intent.status == "submitted":
                raise ValueError("work item already has a committed external submission intent")
            intent = self.store.mark_sending(work.id)
            sending_started = True
            payload = {
                "work_item_id": work.id,
                "opportunity_id": work.opportunity_id,
                "objective": work.objective,
                "deliverable": artifact,
                "acceptance_criteria": work.acceptance_criteria,
                "idempotency_key": intent.idempotency_key,
            }
            raw = self.mcp.call(
                str(port["server"]),
                str(port["submit_tool"]),
                payload,
            )
            result = ToolResult(raw.ok, capability, raw.data, raw.error)
            structured = self._structured(result)
            echoed_key = str(structured.get("idempotency_key", "")).strip()
            if echoed_key != intent.idempotency_key:
                raise ValueError(
                    "submission adapter did not echo the exact idempotency_key; remote dedupe contract is unproven"
                )
            submission_ref = str(structured.get("submission_ref", "")).strip()
            if not submission_ref:
                raise ValueError("submission adapter did not return submission_ref")
            duration_ms = (time.monotonic() - started) * 1000.0
            observation_id = self._record_observation(
                capability=capability,
                arguments=args,
                result=result,
                transaction_id=transaction_id,
                duration_ms=duration_ms,
            )
            submission = self.store.finalize_submission(
                work_item_id=work.id,
                port_name=str(port_name),
                observation_id=observation_id,
                submission_ref=submission_ref,
                response=structured,
            )
            self.resource_ecology.record_submission(
                work_item_id=work.id,
                observation_id=observation_id,
                evidence=(
                    f"Configured idempotent work port {port_name!r} returned "
                    f"submission_ref={submission_ref!r}."
                ),
            )
            self.state_bus.commit(
                transaction_id,
                {
                    "capability": capability,
                    "success": True,
                    "observation_id": observation_id,
                    "work_item_id": work.id,
                    "submission_id": submission.id,
                    "idempotency_key": intent.idempotency_key,
                },
            )
            return ToolResult(
                True,
                capability,
                {
                    "work_item_id": work.id,
                    "submission_id": submission.id,
                    "submission_ref": submission.submission_ref,
                    "remote_status": submission.remote_status,
                    "observation_id": observation_id,
                    "idempotency_key": intent.idempotency_key,
                },
            )
        except Exception as exc:
            if sending_started:
                try:
                    self.store.mark_indeterminate(work_item_id, f"{type(exc).__name__}: {exc}")
                except Exception:
                    pass
            duration_ms = (time.monotonic() - started) * 1000.0
            result = ToolResult(
                False,
                capability,
                error=f"{type(exc).__name__}: {str(exc)[:2000]}",
            )
            try:
                observation_id = self._record_observation(
                    capability=capability,
                    arguments=args,
                    result=result,
                    transaction_id=transaction_id,
                    duration_ms=duration_ms,
                )
                self.state_bus.commit(
                    transaction_id,
                    {
                        "capability": capability,
                        "success": False,
                        "observation_id": observation_id,
                        "external_outcome": "indeterminate" if sending_started else "not_sent",
                    },
                )
            except Exception:
                try:
                    self.state_bus.abort(transaction_id, result.error or "work-port submit failed")
                except Exception:
                    pass
            return result

    def reconcile_submission(self, work_item_id: int) -> ToolResult:
        capability = "reconcile_work_submission"
        args = {"work_item_id": int(work_item_id)}
        transaction_id = self.state_bus.begin("work_port:reconcile")
        started = time.monotonic()
        try:
            if not self.enabled:
                raise RuntimeError(f"work ports unavailable: {self._readiness()}")
            intent = self.store.intent_for_work(int(work_item_id))
            if intent is None:
                raise ValueError("work item has no durable submission intent")
            if intent.status == "submitted":
                submission = self.store.submission_for_work(intent.work_item_id)
                if submission is None:
                    raise RuntimeError("submitted intent has no local submission row")
                self.state_bus.commit(
                    transaction_id,
                    {"capability": capability, "success": True, "already_reconciled": True},
                )
                return ToolResult(True, capability, submission.as_dict())
            if intent.status not in {"sending", "indeterminate"}:
                raise ValueError(f"submission intent is not ambiguous: {intent.status}")
            if intent.status == "sending":
                intent = self.store.mark_indeterminate(
                    intent.work_item_id,
                    "reconciliation requested for interrupted sending state",
                )
            port = self._port(intent.port_name)
            raw = self.mcp.call(
                str(port["server"]),
                str(port["lookup_tool"]),
                {
                    "work_item_id": intent.work_item_id,
                    "idempotency_key": intent.idempotency_key,
                },
            )
            result = ToolResult(raw.ok, capability, raw.data, raw.error)
            structured = self._structured(result)
            echoed_key = str(structured.get("idempotency_key", "")).strip()
            if echoed_key != intent.idempotency_key:
                raise ValueError("lookup adapter did not echo exact idempotency_key")
            status = str(structured.get("status", "")).strip().lower()
            if status not in {"not_found", "submitted", "pending", "accepted", "rejected"}:
                raise ValueError(
                    "lookup adapter status must be not_found|submitted|pending|accepted|rejected"
                )
            duration_ms = (time.monotonic() - started) * 1000.0
            observation_id = self._record_observation(
                capability=capability,
                arguments=args,
                result=result,
                transaction_id=transaction_id,
                duration_ms=duration_ms,
            )
            if status == "not_found":
                reset = self.store.mark_not_found_after_lookup(intent.work_item_id)
                self.state_bus.commit(
                    transaction_id,
                    {
                        "capability": capability,
                        "success": True,
                        "remote_status": "not_found",
                        "intent_status": reset.status,
                        "observation_id": observation_id,
                    },
                )
                return ToolResult(
                    True,
                    capability,
                    {
                        "work_item_id": intent.work_item_id,
                        "remote_status": "not_found",
                        "intent_status": reset.status,
                        "retry_permitted": True,
                        "observation_id": observation_id,
                    },
                )

            submission_ref = str(structured.get("submission_ref", "")).strip()
            if not submission_ref:
                raise ValueError("lookup found remote submission without submission_ref")
            submission = self.store.finalize_submission(
                work_item_id=intent.work_item_id,
                port_name=intent.port_name,
                observation_id=observation_id,
                submission_ref=submission_ref,
                response=structured,
            )
            work = self.resource_ecology.work_item(intent.work_item_id)
            if work is not None and work.status == "staged":
                self.resource_ecology.record_submission(
                    work_item_id=work.id,
                    observation_id=observation_id,
                    evidence=(
                        "Remote idempotency lookup recovered a previously ambiguous "
                        f"submission_ref={submission_ref!r}."
                    ),
                )
            if status in {"pending", "accepted", "rejected"}:
                submission = self.store.record_outcome(
                    work_item_id=intent.work_item_id,
                    observation_id=observation_id,
                    status=status,
                    response=structured,
                )
            if status in {"accepted", "rejected"}:
                evidence = str(structured.get("evidence", "")).strip()[:8000]
                if not evidence:
                    raise ValueError("terminal reconciled outcome requires evidence")
                work = self.resource_ecology.work_item(intent.work_item_id)
                if work is not None and work.status == "submitted":
                    self.resource_ecology.record_external_outcome(
                        work_item_id=work.id,
                        accepted=status == "accepted",
                        evidence=f"Configured work port {intent.port_name!r}: {evidence}",
                    )
            self.state_bus.commit(
                transaction_id,
                {
                    "capability": capability,
                    "success": True,
                    "work_item_id": intent.work_item_id,
                    "submission_id": submission.id,
                    "remote_status": status,
                    "observation_id": observation_id,
                },
            )
            return ToolResult(
                True,
                capability,
                {
                    "work_item_id": intent.work_item_id,
                    "submission_id": submission.id,
                    "submission_ref": submission.submission_ref,
                    "remote_status": submission.remote_status,
                    "observation_id": observation_id,
                    "idempotency_key": intent.idempotency_key,
                },
            )
        except Exception as exc:
            result = ToolResult(
                False,
                capability,
                error=f"{type(exc).__name__}: {str(exc)[:2000]}",
            )
            try:
                self.state_bus.commit(
                    transaction_id,
                    {"capability": capability, "success": False},
                )
            except Exception:
                try:
                    self.state_bus.abort(transaction_id, result.error or "reconciliation failed")
                except Exception:
                    pass
            return result

    def check_outcome(self, work_item_id: int) -> ToolResult:
        capability = "check_work_outcome"
        args = {"work_item_id": int(work_item_id)}
        transaction_id = self.state_bus.begin("work_port:outcome")
        self.state_bus.append(
            transaction_id,
            phase="action",
            kind="WORK_PORT_ATTEMPT",
            payload={"capability": capability, "arguments_fingerprint": _fingerprint(args)},
        )
        started = time.monotonic()
        try:
            if not self.enabled:
                raise RuntimeError(f"work ports unavailable: {self._readiness()}")
            submission = self.store.submission_for_work(int(work_item_id))
            if submission is None:
                intent = self.store.intent_for_work(int(work_item_id))
                if intent is not None and intent.status in {"sending", "indeterminate"}:
                    raise RuntimeError(
                        "submission outcome is indeterminate; reconcile_work_submission first"
                    )
                raise ValueError("work item has no external submission")
            port = self._port(submission.port_name)
            raw = self.mcp.call(
                str(port["server"]),
                str(port["outcome_tool"]),
                {
                    "work_item_id": submission.work_item_id,
                    "submission_ref": submission.submission_ref,
                },
            )
            result = ToolResult(raw.ok, capability, raw.data, raw.error)
            structured = self._structured(result)
            status = str(structured.get("status", "")).strip().lower()
            if status not in {"pending", "accepted", "rejected"}:
                raise ValueError("outcome adapter status must be pending|accepted|rejected")
            evidence = str(structured.get("evidence", "")).strip()[:8000]
            if status in {"accepted", "rejected"} and not evidence:
                raise ValueError("terminal external outcome requires evidence")
            duration_ms = (time.monotonic() - started) * 1000.0
            observation_id = self._record_observation(
                capability=capability,
                arguments=args,
                result=result,
                transaction_id=transaction_id,
                duration_ms=duration_ms,
            )
            updated = self.store.record_outcome(
                work_item_id=submission.work_item_id,
                observation_id=observation_id,
                status=status,
                response=structured,
            )
            if status in {"accepted", "rejected"}:
                work = self.resource_ecology.work_item(submission.work_item_id)
                if work is not None and work.status == "submitted":
                    self.resource_ecology.record_external_outcome(
                        work_item_id=submission.work_item_id,
                        accepted=status == "accepted",
                        evidence=(
                            f"Configured work port {submission.port_name!r}: {evidence}"
                        ),
                    )
            self.state_bus.commit(
                transaction_id,
                {
                    "capability": capability,
                    "success": True,
                    "observation_id": observation_id,
                    "work_item_id": submission.work_item_id,
                    "remote_status": status,
                },
            )
            return ToolResult(
                True,
                capability,
                {
                    "work_item_id": submission.work_item_id,
                    "submission_id": updated.id,
                    "submission_ref": updated.submission_ref,
                    "remote_status": updated.remote_status,
                    "observation_id": observation_id,
                },
            )
        except Exception as exc:
            duration_ms = (time.monotonic() - started) * 1000.0
            result = ToolResult(
                False,
                capability,
                error=f"{type(exc).__name__}: {str(exc)[:2000]}",
            )
            try:
                observation_id = self._record_observation(
                    capability=capability,
                    arguments=args,
                    result=result,
                    transaction_id=transaction_id,
                    duration_ms=duration_ms,
                )
                self.state_bus.commit(
                    transaction_id,
                    {"capability": capability, "success": False, "observation_id": observation_id},
                )
            except Exception:
                try:
                    self.state_bus.abort(transaction_id, result.error or "work-port outcome failed")
                except Exception:
                    pass
            return result

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        work_item_id_raw = args.get("work_item_id")
        if name in {
            "submit_work",
            "check_work_outcome",
            "reconcile_work_submission",
        } and work_item_id_raw is None:
            return ToolResult(False, name, error="work_item_id is required")
        try:
            work_item_id = int(work_item_id_raw) if work_item_id_raw is not None else 0
        except (TypeError, ValueError):
            return ToolResult(False, name, error="work_item_id must be an integer")
        if name in {
            "submit_work",
            "check_work_outcome",
            "reconcile_work_submission",
        } and work_item_id < 1:
            return ToolResult(False, name, error="work_item_id must be positive")
        if name == "submit_work":
            return self.submit(str(args.get("port", "")), work_item_id)
        if name == "check_work_outcome":
            return self.check_outcome(work_item_id)
        if name == "reconcile_work_submission":
            return self.reconcile_submission(work_item_id)
        return ToolResult(False, name, error=f"Unknown work-port capability: {name}")
