from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

from .body.mcp import MCPBody
from .causal import CausalMemoryStore
from .economy import EconomyStore
from .observations import ObservationStore
from .resource_ecology import ResourceEcologyStore
from .state_bus import OrganismStateBus
from .tools import Capability, ToolResult
from .verification import VerificationRegistry


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


def _fingerprint(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _clean(value: Any, *, field: str, maximum: int = 128) -> str:
    text = str(value).strip()[:maximum]
    if not text:
        raise ValueError(f"{field} is required")
    return text


@dataclass(frozen=True, slots=True)
class IngressRecord:
    id: int
    verifier_name: str
    external_event_id: str
    external_event_sha256: str
    first_seen_at: str
    updated_at: str
    status: str
    asset: str
    unit: str
    amount: float
    kind: str
    source: str
    evidence_sha256: str
    observation_id: int
    resource_event_id: int | None
    work_item_id: int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResourceIngressStore:
    """Replay ledger for externally observed resource-ingress events."""

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
                CREATE TABLE IF NOT EXISTS resource_ingress_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    verifier_name TEXT NOT NULL,
                    external_event_id TEXT NOT NULL,
                    external_event_sha256 TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'observed',
                    asset TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    amount REAL NOT NULL,
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    observation_id INTEGER NOT NULL,
                    resource_event_id INTEGER NULL,
                    work_item_id INTEGER NULL,
                    UNIQUE(verifier_name, external_event_sha256),
                    FOREIGN KEY(resource_event_id) REFERENCES resource_events(id),
                    FOREIGN KEY(work_item_id) REFERENCES ecology_work_items(id)
                );
                CREATE INDEX IF NOT EXISTS idx_resource_ingress_status
                    ON resource_ingress_events(status, updated_at, id);
                """
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> IngressRecord:
        return IngressRecord(
            id=int(row["id"]),
            verifier_name=str(row["verifier_name"]),
            external_event_id=str(row["external_event_id"]),
            external_event_sha256=str(row["external_event_sha256"]),
            first_seen_at=str(row["first_seen_at"]),
            updated_at=str(row["updated_at"]),
            status=str(row["status"]),
            asset=str(row["asset"]),
            unit=str(row["unit"]),
            amount=float(row["amount"]),
            kind=str(row["kind"]),
            source=str(row["source"]),
            evidence_sha256=str(row["evidence_sha256"]),
            observation_id=int(row["observation_id"]),
            resource_event_id=(
                int(row["resource_event_id"]) if row["resource_event_id"] is not None else None
            ),
            work_item_id=(int(row["work_item_id"]) if row["work_item_id"] is not None else None),
        )

    def get(self, verifier_name: str, external_event_id: str) -> IngressRecord | None:
        digest = sha256(str(external_event_id).encode("utf-8")).hexdigest()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM resource_ingress_events
                WHERE verifier_name=? AND external_event_sha256=?
                """,
                (str(verifier_name), digest),
            ).fetchone()
        return self._from_row(row) if row else None

    def reserve(
        self,
        *,
        verifier_name: str,
        external_event_id: str,
        asset: str,
        unit: str,
        amount: float,
        kind: str,
        source: str,
        evidence: str,
        observation_id: int,
        work_item_id: int | None,
    ) -> tuple[IngressRecord, bool]:
        event_id = _clean(external_event_id, field="external_event_id", maximum=2000)
        digest = sha256(event_id.encode("utf-8")).hexdigest()
        evidence_sha256 = sha256(str(evidence).encode("utf-8")).hexdigest()
        timestamp = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT * FROM resource_ingress_events
                WHERE verifier_name=? AND external_event_sha256=?
                """,
                (verifier_name, digest),
            ).fetchone()
            if existing is not None:
                record = self._from_row(existing)
                expected = {
                    "asset": asset,
                    "unit": unit,
                    "amount": float(amount),
                    "kind": kind,
                    "source": source,
                    "evidence_sha256": evidence_sha256,
                    "work_item_id": work_item_id,
                }
                actual = {
                    "asset": record.asset,
                    "unit": record.unit,
                    "amount": record.amount,
                    "kind": record.kind,
                    "source": record.source,
                    "evidence_sha256": record.evidence_sha256,
                    "work_item_id": record.work_item_id,
                }
                if actual != expected:
                    raise PermissionError(
                        "replayed external_event_id conflicts with the originally observed ingress claim"
                    )
                return record, False
            cur = conn.execute(
                """
                INSERT INTO resource_ingress_events(
                    verifier_name, external_event_id, external_event_sha256,
                    first_seen_at, updated_at, status, asset, unit, amount, kind,
                    source, evidence_sha256, observation_id, work_item_id
                ) VALUES (?, ?, ?, ?, ?, 'observed', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    verifier_name,
                    event_id,
                    digest,
                    timestamp,
                    timestamp,
                    asset,
                    unit,
                    float(amount),
                    kind,
                    source,
                    evidence_sha256,
                    int(observation_id),
                    int(work_item_id) if work_item_id is not None else None,
                ),
            )
            row = conn.execute(
                "SELECT * FROM resource_ingress_events WHERE id=?", (int(cur.lastrowid),)
            ).fetchone()
        if row is None:
            raise RuntimeError("ingress reservation disappeared after insert")
        return self._from_row(row), True

    def mark_realized(self, ingress_id: int, resource_event_id: int) -> IngressRecord:
        timestamp = _now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE resource_ingress_events
                SET updated_at=?, status='realized', resource_event_id=?
                WHERE id=?
                """,
                (timestamp, int(resource_event_id), int(ingress_id)),
            )
            row = conn.execute(
                "SELECT * FROM resource_ingress_events WHERE id=?", (int(ingress_id),)
            ).fetchone()
        if row is None:
            raise RuntimeError("ingress record disappeared after realization")
        return self._from_row(row)

    def recent(self, limit: int = 64) -> list[IngressRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM resource_ingress_events ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 512)),),
            ).fetchall()
        return [self._from_row(row) for row in reversed(rows)]


class ResourceIngressRegistry:
    """Independent configured verifier ports for positive resource ingress.

    Verifier configuration fixes MCP server/tool, target asset/unit/kind and HMAC trust
    key environment variable. The model can request a check by verifier name and
    optional accepted work_item_id but cannot supply amount, unit, event ID, evidence,
    authority, signature or verifier key.
    """

    def __init__(
        self,
        state_dir: Path,
        tool_config: dict[str, Any] | None = None,
        *,
        mcp_target_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.state_dir = Path(state_dir).resolve()
        self.workspace = (self.state_dir / "workspace").resolve()
        self.config = dict((tool_config or {}).get("resource_ingress") or {})
        database = self.state_dir / "memory.sqlite3"
        # ResourceIngress has foreign-key references into the economy and ecology
        # domains. Initialize those owning schemas first so a clean state directory
        # cannot reach the verifier with dangling parent-table definitions.
        EconomyStore(database)
        self.resource_ecology = ResourceEcologyStore(database)
        self.store = ResourceIngressStore(database)
        self.observations = ObservationStore(database)
        self.causal = CausalMemoryStore(database)
        self.state_bus = OrganismStateBus(database)
        body = dict((tool_config or {}).get("body") or {})
        self.mcp = MCPBody(
            dict(body.get("mcp") or {}),
            target_overrides=mcp_target_overrides,
        )

    def verifiers(self) -> dict[str, dict[str, Any]]:
        raw = self.config.get("verifiers") or {}
        if not isinstance(raw, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for name, item in raw.items():
            if not isinstance(item, dict) or not bool(item.get("enabled", True)):
                continue
            cleaned = str(name).strip()[:128]
            if cleaned:
                result[cleaned] = dict(item)
        return result

    def _verifier(self, name: str) -> dict[str, Any]:
        item = self.verifiers().get(str(name))
        if item is None:
            raise ValueError(f"unknown or disabled resource verifier: {name!r}")
        for field in ("server", "tool", "authority", "key_env", "asset", "unit", "kind"):
            if not str(item.get(field, "")).strip():
                raise ValueError(f"resource verifier {name!r} has no {field}")
        return item

    @staticmethod
    def _machine_object(result: ToolResult) -> dict[str, Any]:
        if not result.ok or not isinstance(result.data, dict):
            raise RuntimeError(result.error or "resource verifier MCP call failed")
        item = result.data.get("structured_content")
        if not isinstance(item, dict):
            raise ValueError("resource verifier must return a machine-readable JSON object")
        return dict(item)

    def _key(self, verifier: dict[str, Any]) -> bytes:
        env_name = str(verifier["key_env"]).strip()
        key = os.getenv(env_name)
        if key is None:
            raise RuntimeError(f"resource verifier key environment variable is missing: {env_name}")
        raw = key.encode("utf-8")
        if len(raw) < 16:
            raise RuntimeError(f"resource verifier key {env_name!r} must be at least 16 bytes")
        server = self.mcp.servers().get(str(verifier["server"]), {})
        header_envs = {
            str(value).strip()
            for value in dict(server.get("headers_from_env") or {}).values()
        }
        if env_name in header_envs:
            raise RuntimeError(
                "resource verifier signing key must not be delegated as an MCP transport credential"
            )
        return raw

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False)) and bool(self.verifiers()) and self.mcp.enabled

    def _readiness(self) -> str:
        if not bool(self.config.get("enabled", False)):
            return "disabled"
        if not self.verifiers():
            return "no_configured_verifiers"
        if not self.mcp.installed:
            return "mcp_v2_not_installed"
        if not self.mcp.enabled:
            return "configured_mcp_body_unavailable"
        for item in self.verifiers().values():
            if os.getenv(str(item.get("key_env", "")).strip()) is None:
                return "verifier_key_missing"
        return "ready"

    def catalog(self) -> dict[str, dict[str, Any]]:
        return {
            "check_resource_ingress": Capability(
                "check_resource_ingress",
                "Read one preconfigured verifier source and ingest one observed positive resource event if new.",
                "{verifier: configured_name, work_item_id?: accepted_work_id}",
                "configured_resource_verification",
                "may add a verified positive resource only from independent observed verifier evidence",
                "configured_resource_verifier",
                "network",
                enabled=self.enabled,
                readiness=self._readiness(),
            ).as_dict()
        }

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "readiness": self._readiness(),
            "verifiers": {
                name: {
                    "server": str(item.get("server", "")),
                    "tool": str(item.get("tool", "")),
                    "authority": str(item.get("authority", "")),
                    "asset": str(item.get("asset", "")),
                    "unit": str(item.get("unit", "")),
                    "kind": str(item.get("kind", "")),
                    "key_present": os.getenv(str(item.get("key_env", "")).strip()) is not None,
                }
                for name, item in self.verifiers().items()
            },
            "recent": [
                {
                    "id": item.id,
                    "verifier_name": item.verifier_name,
                    "external_event_sha256": item.external_event_sha256,
                    "status": item.status,
                    "asset": item.asset,
                    "unit": item.unit,
                    "amount": item.amount,
                    "observation_id": item.observation_id,
                    "resource_event_id": item.resource_event_id,
                    "work_item_id": item.work_item_id,
                }
                for item in self.store.recent(32)
            ],
        }

    def _validate_work_target(
        self,
        work_item_id: int | None,
        *,
        asset: str,
        unit: str,
    ) -> None:
        if work_item_id is None:
            return
        work = self.resource_ecology.work_item(int(work_item_id))
        if work is None:
            raise ValueError(f"work item does not exist: {work_item_id}")
        if work.status not in {"accepted", "realized"}:
            raise ValueError("linked resource ingress requires accepted or already-realized work")
        profile = self.resource_ecology.profile(work.opportunity_id)
        if profile is None:
            raise ValueError("linked work has no resource profile")
        if profile.target_asset != asset or profile.target_unit != unit:
            raise ValueError("resource verifier target does not match accepted work resource profile")

    def _record_observation(
        self,
        *,
        verifier_name: str,
        work_item_id: int | None,
        result: ToolResult,
        transaction_id: str,
        duration_ms: float,
    ) -> int:
        arguments = {
            "verifier": verifier_name,
            "work_item_id": int(work_item_id) if work_item_id is not None else None,
        }
        observation = self.observations.record(
            source_kind="resource_ingress",
            source_ref="check_resource_ingress",
            payload=result.as_dict(),
            trust=0.95,
            success=result.ok,
            summary=(result.error or "resource verifier check completed")[:4000],
            provenance={
                "verifier": verifier_name,
                "arguments_fingerprint": _fingerprint(arguments),
                "authority": "configured_resource_verifier",
            },
            transaction_id=transaction_id,
        )
        experience = self.causal.record_intervention(
            action_name="check_resource_ingress",
            arguments=arguments,
            outcome=result.as_dict(),
            success=result.ok,
            duration_ms=duration_ms,
            observation_id=observation.id,
            transaction_id=transaction_id,
            source="resource_ingress_registry",
            outcome_summary=result.error or f"resource verifier {verifier_name} ok={result.ok}",
        )
        self.state_bus.append(
            transaction_id,
            phase="observation",
            kind="RESOURCE_INGRESS_OBSERVATION",
            payload={
                "verifier": verifier_name,
                "observation_id": observation.id,
                "payload_sha256": observation.payload_sha256,
                "experience_id": experience.id,
                "success": result.ok,
            },
        )
        return observation.id

    def _deterministic_source(self, verifier_name: str, external_event_id: str) -> str:
        digest = sha256(str(external_event_id).encode("utf-8")).hexdigest()[:32]
        return f"ingress:{verifier_name}:{digest}"[:128]

    def _existing_resource_event(self, source: str) -> sqlite3.Row | None:
        with self.store._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, asset, unit, amount, kind, verified, source, evidence
                FROM resource_events WHERE source=? ORDER BY id ASC
                """,
                (source,),
            ).fetchall()
        if len(rows) > 1:
            raise RuntimeError("resource ingress deterministic source maps to multiple resource events")
        return rows[0] if rows else None

    def check(self, verifier_name: str, work_item_id: int | None = None) -> ToolResult:
        capability = "check_resource_ingress"
        args = {
            "verifier": str(verifier_name),
            "work_item_id": int(work_item_id) if work_item_id is not None else None,
        }
        transaction_id = self.state_bus.begin("resource_ingress:check")
        self.state_bus.append(
            transaction_id,
            phase="action",
            kind="RESOURCE_INGRESS_ATTEMPT",
            payload={"arguments_fingerprint": _fingerprint(args)},
        )
        started = time.monotonic()
        try:
            if not self.enabled:
                raise RuntimeError(f"resource ingress unavailable: {self._readiness()}")
            verifier = self._verifier(verifier_name)
            asset = _clean(verifier["asset"], field="asset")
            unit = _clean(verifier["unit"], field="unit", maximum=64)
            kind = _clean(verifier["kind"], field="kind", maximum=64)
            authority = _clean(verifier["authority"], field="authority", maximum=256)
            key = self._key(verifier)
            self._validate_work_target(work_item_id, asset=asset, unit=unit)

            raw = self.mcp.call(
                str(verifier["server"]),
                str(verifier["tool"]),
                {
                    "work_item_id": int(work_item_id) if work_item_id is not None else None,
                    "asset": asset,
                    "unit": unit,
                },
            )
            result = ToolResult(raw.ok, capability, raw.data, raw.error)
            structured = self._machine_object(result)
            if not bool(structured.get("observed", True)):
                duration_ms = (time.monotonic() - started) * 1000.0
                observation_id = self._record_observation(
                    verifier_name=verifier_name,
                    work_item_id=work_item_id,
                    result=result,
                    transaction_id=transaction_id,
                    duration_ms=duration_ms,
                )
                self.state_bus.commit(
                    transaction_id,
                    {
                        "success": True,
                        "new_resource": False,
                        "observation_id": observation_id,
                    },
                )
                return ToolResult(
                    True,
                    capability,
                    {"new_resource": False, "observation_id": observation_id},
                )

            external_event_id = _clean(
                structured.get("external_event_id", ""),
                field="external_event_id",
                maximum=2000,
            )
            amount = float(structured.get("amount", 0.0))
            if not math.isfinite(amount) or amount <= 0:
                raise ValueError("resource verifier amount must be a finite positive number")
            provider_evidence = _clean(
                structured.get("evidence", ""), field="provider evidence", maximum=8000
            )
            evidence = _canonical(
                {
                    "verifier": str(verifier_name),
                    "external_event_id": external_event_id,
                    "provider_evidence": provider_evidence,
                    "asset": asset,
                    "unit": unit,
                    "amount": amount,
                    "kind": kind,
                }
            )
            source = self._deterministic_source(verifier_name, external_event_id)
            duration_ms = (time.monotonic() - started) * 1000.0
            observation_id = self._record_observation(
                verifier_name=verifier_name,
                work_item_id=work_item_id,
                result=result,
                transaction_id=transaction_id,
                duration_ms=duration_ms,
            )
            reservation, created = self.store.reserve(
                verifier_name=str(verifier_name),
                external_event_id=external_event_id,
                asset=asset,
                unit=unit,
                amount=amount,
                kind=kind,
                source=source,
                evidence=evidence,
                observation_id=observation_id,
                work_item_id=work_item_id,
            )

            existing = self._existing_resource_event(source)
            if existing is not None:
                if not bool(existing["verified"]):
                    raise PermissionError("deterministic ingress source already exists as unverified event")
                expected = (
                    asset,
                    unit,
                    amount,
                    kind,
                    source,
                    evidence,
                )
                actual = (
                    str(existing["asset"]),
                    str(existing["unit"]),
                    float(existing["amount"]),
                    str(existing["kind"]),
                    str(existing["source"]),
                    str(existing["evidence"]),
                )
                if actual != expected:
                    raise PermissionError("existing verified ingress event conflicts with current verifier evidence")
                resource_event_id = int(existing["id"])
            else:
                registry = VerificationRegistry({authority: key})
                economy = EconomyStore(self.store.path, verification_registry=registry)
                claim = EconomyStore.resource_claim(
                    asset=asset,
                    unit=unit,
                    amount=amount,
                    kind=kind,
                    source=source,
                )
                receipt = registry.issue(authority, claim=claim, evidence=evidence)
                resource_event_id = economy.record_resource_event(
                    asset=asset,
                    unit=unit,
                    amount=amount,
                    kind=kind,
                    source=source,
                    evidence=evidence,
                    verified=True,
                    verification_receipt=receipt,
                )

            realized = self.store.mark_realized(reservation.id, resource_event_id)
            if work_item_id is not None:
                work = self.resource_ecology.work_item(int(work_item_id))
                if work is None:
                    raise RuntimeError("linked work disappeared during resource realization")
                if work.status == "accepted":
                    self.resource_ecology.link_verified_resource_event(
                        work_item_id=int(work_item_id),
                        resource_event_id=resource_event_id,
                        evidence=(
                            f"Independent resource verifier {verifier_name!r} observed external event "
                            f"{reservation.external_event_sha256}."
                        ),
                    )
                elif work.status == "realized":
                    if work.resource_event_id != resource_event_id:
                        raise PermissionError(
                            "replayed ingress conflicts with the resource event already linked to realized work"
                        )
                else:
                    raise PermissionError(
                        "linked work changed to an invalid state during resource realization"
                    )
            self.state_bus.commit(
                transaction_id,
                {
                    "success": True,
                    "new_resource": created and existing is None,
                    "observation_id": observation_id,
                    "ingress_id": realized.id,
                    "resource_event_id": resource_event_id,
                    "work_item_id": work_item_id,
                },
            )
            return ToolResult(
                True,
                capability,
                {
                    "new_resource": created and existing is None,
                    "replayed": not (created and existing is None),
                    "ingress_id": realized.id,
                    "resource_event_id": resource_event_id,
                    "asset": asset,
                    "unit": unit,
                    "amount": amount,
                    "work_item_id": work_item_id,
                    "observation_id": observation_id,
                },
            )
        except Exception as exc:
            result = ToolResult(False, capability, error=f"{type(exc).__name__}: {str(exc)[:2000]}")
            try:
                duration_ms = (time.monotonic() - started) * 1000.0
                observation_id = self._record_observation(
                    verifier_name=str(verifier_name),
                    work_item_id=work_item_id,
                    result=result,
                    transaction_id=transaction_id,
                    duration_ms=duration_ms,
                )
                self.state_bus.commit(
                    transaction_id,
                    {"success": False, "observation_id": observation_id},
                )
            except Exception:
                try:
                    self.state_bus.abort(transaction_id, result.error or "resource ingress failed")
                except Exception:
                    pass
            return result

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name != "check_resource_ingress":
            return ToolResult(False, name, error=f"Unknown resource-ingress capability: {name}")
        work_raw = args.get("work_item_id")
        work_item_id = int(work_raw) if work_raw is not None else None
        return self.check(str(args.get("verifier", "")), work_item_id)
