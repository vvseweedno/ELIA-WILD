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
from .causal import CausalMemoryStore
from .observations import ObservationStore
from .resource_ecology import ResourceEcologyStore, WorkItem
from .state_bus import OrganismStateBus
from .tools import Capability, ToolResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


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
    """Durable bridge between local work lifecycle and configured external ports."""

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

    def submission_for_work(self, work_item_id: int) -> WorkPortSubmission | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM work_port_submissions WHERE work_item_id=?",
                (int(work_item_id),),
            ).fetchone()
        return self._from_row(row) if row else None

    def record_submission(
        self,
        *,
        work_item_id: int,
        port_name: str,
        observation_id: int,
        submission_ref: str,
        response: dict[str, Any],
    ) -> WorkPortSubmission:
        submission_ref = str(submission_ref).strip()[:2000]
        if not submission_ref:
            raise ValueError("submission_ref is required")
        timestamp = _now()
        with self._connect() as conn:
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
            row = conn.execute(
                "SELECT * FROM work_port_submissions WHERE id=?", (int(cur.lastrowid),)
            ).fetchone()
        if row is None:
            raise RuntimeError("work-port submission disappeared after insert")
        return self._from_row(row)

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


class WorkPortRegistry:
    """Authorized external-work ports over fixed MCP server/tool bindings.

    The model chooses only a configured port name + work_item_id. It never supplies
    arbitrary MCP server/tool names. Submission/outcome calls are recorded as durable
    Observations and causal interventions before they advance Resource Ecology state.
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

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False)) and bool(self.ports()) and self.mcp.enabled

    def _readiness(self) -> str:
        if not bool(self.config.get("enabled", False)):
            return "disabled"
        if not self.ports():
            return "no_configured_ports"
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
                "Submit one staged work item through one preconfigured external work port.",
                "{port: configured_name, work_item_id: int}",
                "configured_external_submission",
                "may create an external submission through the fixed port binding",
                "configured_work_port",
                "network",
                enabled=enabled,
                readiness=readiness,
            ).as_dict(),
            "check_work_outcome": Capability(
                "check_work_outcome",
                "Poll the configured external work port for one previously submitted work item.",
                "{work_item_id: int}",
                "configured_external_outcome_read",
                "reads remote submission status; accepted/rejected may update local work lifecycle",
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
                }
                for name, item in self.ports().items()
            },
            "active_submissions": [item.as_dict() for item in self.store.active(32)],
        }

    def _port(self, name: str) -> dict[str, Any]:
        port = self.ports().get(str(name))
        if port is None:
            raise ValueError(f"unknown or disabled work port: {name!r}")
        for field in ("server", "submit_tool", "outcome_tool"):
            if not str(port.get(field, "")).strip():
                raise ValueError(f"work port {name!r} has no {field}")
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
            min(int(port.get("max_artifact_bytes", self.MAX_ARTIFACT_BYTES)), self.MAX_ARTIFACT_BYTES),
        )
        payload = path.read_bytes()
        if len(payload) > max_bytes:
            raise ValueError(f"staged artifact exceeds work-port limit: {len(payload)} > {max_bytes}")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Genesis 1.5 work ports currently accept UTF-8 artifacts only") from exc
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
            payload = {
                "work_item_id": work.id,
                "opportunity_id": work.opportunity_id,
                "objective": work.objective,
                "deliverable": artifact,
                "acceptance_criteria": work.acceptance_criteria,
            }
            raw = self.mcp.call(
                str(port["server"]),
                str(port["submit_tool"]),
                payload,
            )
            result = ToolResult(raw.ok, capability, raw.data, raw.error)
            structured = self._structured(result)
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
            self.resource_ecology.record_submission(
                work_item_id=work.id,
                observation_id=observation_id,
                evidence=(
                    f"Configured work port {port_name!r} returned submission_ref={submission_ref!r}."
                ),
            )
            submission = self.store.record_submission(
                work_item_id=work.id,
                port_name=str(port_name),
                observation_id=observation_id,
                submission_ref=submission_ref,
                response=structured,
            )
            self.state_bus.commit(
                transaction_id,
                {
                    "capability": capability,
                    "success": True,
                    "observation_id": observation_id,
                    "work_item_id": work.id,
                    "submission_id": submission.id,
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
                },
            )
        except Exception as exc:
            duration_ms = (time.monotonic() - started) * 1000.0
            result = ToolResult(False, capability, error=f"{type(exc).__name__}: {str(exc)[:2000]}")
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
                    self.state_bus.abort(transaction_id, result.error or "work-port submit failed")
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
            result = ToolResult(False, capability, error=f"{type(exc).__name__}: {str(exc)[:2000]}")
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
        if name == "submit_work":
            return self.submit(str(args.get("port", "")), int(args.get("work_item_id")))
        if name == "check_work_outcome":
            return self.check_outcome(int(args.get("work_item_id")))
        return ToolResult(False, name, error=f"Unknown work-port capability: {name}")
