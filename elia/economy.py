from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import urlparse

from .verification import VerificationReceipt, VerificationRegistry


@dataclass(slots=True)
class ResourceEvent:
    id: int
    timestamp: str
    asset: str
    unit: str
    amount: float
    kind: str
    verified: bool
    source: str
    evidence: str
    verification_authority: str | None


@dataclass(slots=True)
class Opportunity:
    id: int
    created_at: str
    updated_at: str
    title: str
    kind: str
    source_url: str
    evidence: str
    estimated_value: float
    estimated_cost_value: float
    unit: str
    probability: float
    estimated_gpu_hours: float
    status: str
    expires_at: str | None
    notes: str
    source: str

    @property
    def expected_net_value(self) -> float:
        return self.estimated_value * self.probability - self.estimated_cost_value

    @property
    def value_per_gpu_hour(self) -> float:
        return self.expected_net_value / max(self.estimated_gpu_hours, 0.05)

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        item["expected_net_value"] = self.expected_net_value
        item["value_per_gpu_hour"] = self.value_per_gpu_hour
        return item


class EconomyStore:
    """Audited resource and opportunity state sharing ELIA's SQLite database.

    Model-created opportunities are estimates only. A verified resource mutation is
    accepted only when a cryptographic VerificationReceipt authenticates the exact
    normalized claim and evidence against a registry supplied by trusted runtime code.
    A caller-provided authority string is never sufficient to mint verified balance.
    """

    OPPORTUNITY_STATUSES = {
        "discovered",
        "evaluating",
        "pursuing",
        "won",
        "lost",
        "expired",
        "abandoned",
    }
    TERMINAL_STATUSES = {"won", "lost", "expired", "abandoned"}

    def __init__(
        self,
        path: Path,
        verification_registry: VerificationRegistry | None = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.verification_registry = verification_registry
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS resource_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    amount REAL NOT NULL,
                    kind TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '',
                    verification_authority TEXT NULL,
                    verification_receipt_json TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_resource_events_asset_unit
                    ON resource_events(asset, unit, id ASC);
                CREATE INDEX IF NOT EXISTS idx_resource_events_verified
                    ON resource_events(verified, id ASC);

                CREATE TABLE IF NOT EXISTS opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '',
                    estimated_value REAL NOT NULL DEFAULT 0,
                    estimated_cost_value REAL NOT NULL DEFAULT 0,
                    unit TEXT NOT NULL DEFAULT 'VALUE_UNIT',
                    probability REAL NOT NULL DEFAULT 0,
                    estimated_gpu_hours REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'discovered',
                    expires_at TEXT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'brain'
                );
                CREATE INDEX IF NOT EXISTS idx_opportunities_status
                    ON opportunities(status, id ASC);
                CREATE INDEX IF NOT EXISTS idx_opportunities_updated
                    ON opportunities(updated_at DESC);

                CREATE TABLE IF NOT EXISTS opportunity_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    opportunity_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_opportunity_events_opp
                    ON opportunity_events(opportunity_id, id ASC);
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(resource_events)").fetchall()
            }
            if "verification_receipt_json" not in columns:
                conn.execute(
                    "ALTER TABLE resource_events ADD COLUMN verification_receipt_json TEXT NOT NULL DEFAULT ''"
                )

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _clean_name(value: str, *, field: str, maximum: int = 128) -> str:
        cleaned = str(value).strip()[:maximum]
        if not cleaned:
            raise ValueError(f"{field} is required")
        return cleaned

    @staticmethod
    def _finite_nonnegative(value: Any, *, field: str) -> float:
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{field} must be a finite non-negative number")
        return number

    @staticmethod
    def _probability(value: Any) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("probability must be finite")
        return max(0.0, min(1.0, number))

    @staticmethod
    def _public_evidence_url(value: str) -> str:
        url = str(value).strip()[:2000]
        if not url:
            return ""
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("source_url must be an absolute http/https URL")
        if parsed.username or parsed.password:
            raise ValueError("source_url must not contain credentials")
        return url

    @staticmethod
    def resource_claim(
        *, asset: str, unit: str, amount: float, kind: str, source: str
    ) -> dict[str, Any]:
        return {
            "type": "resource_event",
            "asset": str(asset),
            "unit": str(unit),
            "amount": float(amount),
            "kind": str(kind),
            "source": str(source),
        }

    def record_resource_event(
        self,
        *,
        asset: str,
        unit: str,
        amount: float,
        kind: str,
        source: str,
        evidence: str = "",
        verified: bool = False,
        verification_receipt: VerificationReceipt | None = None,
        verification_authority: str | None = None,
    ) -> int:
        asset = self._clean_name(asset, field="asset", maximum=128)
        unit = self._clean_name(unit, field="unit", maximum=64)
        kind = self._clean_name(kind, field="kind", maximum=64)
        source = self._clean_name(source, field="source", maximum=128)
        amount = float(amount)
        if not math.isfinite(amount) or amount == 0:
            raise ValueError("resource amount must be a finite non-zero number")
        evidence = str(evidence).strip()[:8000]

        authority: str | None = None
        receipt_json = ""
        if verified:
            if verification_authority is not None and verification_receipt is None:
                raise ValueError(
                    "verification_authority strings cannot verify resources; a signed VerificationReceipt is required"
                )
            if self.verification_registry is None or verification_receipt is None:
                raise ValueError(
                    "verified resource events require a trusted verification registry and signed VerificationReceipt"
                )
            claim = self.resource_claim(
                asset=asset,
                unit=unit,
                amount=amount,
                kind=kind,
                source=source,
            )
            authority = self.verification_registry.verify(
                verification_receipt,
                claim=claim,
                evidence=evidence,
            )
            receipt_json = json.dumps(
                verification_receipt.as_dict(),
                ensure_ascii=False,
                sort_keys=True,
            )
        elif verification_receipt is not None or verification_authority is not None:
            raise ValueError("unverified resource events must not carry verification credentials")

        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO resource_events(
                    timestamp, asset, unit, amount, kind, verified, source,
                    evidence, verification_authority, verification_receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.now(),
                    asset,
                    unit,
                    amount,
                    kind,
                    1 if verified else 0,
                    source,
                    evidence,
                    authority,
                    receipt_json,
                ),
            )
            return int(cur.lastrowid)

    def verified_balance(self, asset: str, unit: str) -> float:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(amount), 0) AS balance
                FROM resource_events
                WHERE asset=? AND unit=? AND verified=1
                """,
                (str(asset), str(unit)),
            ).fetchone()
        return float(row["balance"] or 0.0)

    def resource_summary(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT asset, unit,
                       COALESCE(SUM(CASE WHEN verified=1 THEN amount ELSE 0 END), 0) AS verified_balance,
                       COALESCE(SUM(CASE WHEN verified=0 THEN amount ELSE 0 END), 0) AS unverified_delta,
                       COUNT(*) AS event_count
                FROM resource_events
                GROUP BY asset, unit
                ORDER BY asset ASC, unit ASC
                """
            ).fetchall()
        return [
            {
                "asset": str(row["asset"]),
                "unit": str(row["unit"]),
                "verified_balance": float(row["verified_balance"] or 0.0),
                "unverified_delta": float(row["unverified_delta"] or 0.0),
                "event_count": int(row["event_count"]),
            }
            for row in rows
        ]

    def create_opportunity(
        self,
        *,
        title: str,
        kind: str,
        source_url: str = "",
        evidence: str = "",
        estimated_value: float = 0,
        estimated_cost_value: float = 0,
        unit: str = "VALUE_UNIT",
        probability: float = 0,
        estimated_gpu_hours: float = 0,
        expires_at: str | None = None,
        notes: str = "",
        source: str = "brain",
    ) -> int:
        title = self._clean_name(title, field="opportunity title", maximum=240)
        kind = self._clean_name(kind, field="opportunity kind", maximum=64)
        source_url = self._public_evidence_url(source_url)
        evidence = str(evidence).strip()[:8000]
        if not source_url and not evidence:
            raise ValueError("opportunity requires a source_url or evidence")
        estimated_value = self._finite_nonnegative(estimated_value, field="estimated_value")
        estimated_cost_value = self._finite_nonnegative(
            estimated_cost_value, field="estimated_cost_value"
        )
        probability = self._probability(probability)
        estimated_gpu_hours = self._finite_nonnegative(
            estimated_gpu_hours, field="estimated_gpu_hours"
        )
        unit = self._clean_name(unit, field="unit", maximum=64)
        source = self._clean_name(source, field="source", maximum=64)
        expires = str(expires_at).strip()[:64] if expires_at else None
        if expires:
            datetime.fromisoformat(expires.replace("Z", "+00:00"))
        timestamp = self.now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO opportunities(
                    created_at, updated_at, title, kind, source_url, evidence,
                    estimated_value, estimated_cost_value, unit, probability,
                    estimated_gpu_hours, status, expires_at, notes, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?, ?, ?)
                """,
                (
                    timestamp,
                    timestamp,
                    title,
                    kind,
                    source_url,
                    evidence,
                    estimated_value,
                    estimated_cost_value,
                    unit,
                    probability,
                    estimated_gpu_hours,
                    expires,
                    str(notes).strip()[:8000],
                    source,
                ),
            )
            opportunity_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO opportunity_events(opportunity_id, timestamp, kind, evidence)
                VALUES (?, ?, 'created', ?)
                """,
                (opportunity_id, timestamp, evidence[:8000]),
            )
            return opportunity_id

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Opportunity:
        return Opportunity(
            id=int(row["id"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            title=str(row["title"]),
            kind=str(row["kind"]),
            source_url=str(row["source_url"]),
            evidence=str(row["evidence"]),
            estimated_value=float(row["estimated_value"]),
            estimated_cost_value=float(row["estimated_cost_value"]),
            unit=str(row["unit"]),
            probability=float(row["probability"]),
            estimated_gpu_hours=float(row["estimated_gpu_hours"]),
            status=str(row["status"]),
            expires_at=str(row["expires_at"]) if row["expires_at"] else None,
            notes=str(row["notes"]),
            source=str(row["source"]),
        )

    def opportunity(self, opportunity_id: int) -> Opportunity | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM opportunities WHERE id=?", (int(opportunity_id),)
            ).fetchone()
        return self._from_row(row) if row else None

    def active_opportunities(self, limit: int = 32) -> list[Opportunity]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM opportunities
                WHERE status IN ('discovered', 'evaluating', 'pursuing')
                ORDER BY id ASC
                LIMIT ?
                """,
                (max(1, min(int(limit), 256)),),
            ).fetchall()
        items = [self._from_row(row) for row in rows]
        items.sort(key=lambda item: (-item.value_per_gpu_hour, -item.expected_net_value, item.id))
        return items

    def update_opportunity(
        self,
        opportunity_id: int,
        *,
        status: str | None = None,
        estimated_value: float | None = None,
        estimated_cost_value: float | None = None,
        probability: float | None = None,
        estimated_gpu_hours: float | None = None,
        evidence: str = "",
        notes: str | None = None,
        event: str = "updated",
    ) -> Opportunity:
        current = self.opportunity(opportunity_id)
        if current is None:
            raise ValueError(f"opportunity does not exist: {opportunity_id}")
        next_status = current.status if status is None else str(status).strip().lower()
        if next_status not in self.OPPORTUNITY_STATUSES:
            raise ValueError(f"invalid opportunity status: {next_status}")
        evidence = str(evidence).strip()[:8000]
        if next_status in self.TERMINAL_STATUSES and next_status != current.status and not evidence:
            raise ValueError(f"marking opportunity {next_status} requires evidence")
        next_value = (
            current.estimated_value
            if estimated_value is None
            else self._finite_nonnegative(estimated_value, field="estimated_value")
        )
        next_cost = (
            current.estimated_cost_value
            if estimated_cost_value is None
            else self._finite_nonnegative(estimated_cost_value, field="estimated_cost_value")
        )
        next_probability = (
            current.probability if probability is None else self._probability(probability)
        )
        next_gpu = (
            current.estimated_gpu_hours
            if estimated_gpu_hours is None
            else self._finite_nonnegative(estimated_gpu_hours, field="estimated_gpu_hours")
        )
        next_notes = current.notes if notes is None else str(notes).strip()[:8000]
        timestamp = self.now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE opportunities
                SET updated_at=?, estimated_value=?, estimated_cost_value=?, probability=?,
                    estimated_gpu_hours=?, status=?, notes=?
                WHERE id=?
                """,
                (
                    timestamp,
                    next_value,
                    next_cost,
                    next_probability,
                    next_gpu,
                    next_status,
                    next_notes,
                    int(opportunity_id),
                ),
            )
            conn.execute(
                """
                INSERT INTO opportunity_events(opportunity_id, timestamp, kind, evidence)
                VALUES (?, ?, ?, ?)
                """,
                (int(opportunity_id), timestamp, str(event)[:64], evidence),
            )
        updated = self.opportunity(opportunity_id)
        if updated is None:
            raise RuntimeError("opportunity disappeared after update")
        return updated

    def opportunity_events(self, opportunity_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, kind, evidence
                FROM opportunity_events
                WHERE opportunity_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(opportunity_id), max(1, min(int(limit), 200))),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "timestamp": str(row["timestamp"]),
                "kind": str(row["kind"]),
                "evidence": str(row["evidence"]),
            }
            for row in reversed(rows)
        ]

    def snapshot(self, opportunity_limit: int = 16) -> dict[str, Any]:
        opportunities = self.active_opportunities(opportunity_limit)
        return {
            "verified_resources": self.resource_summary(),
            "active_opportunities": [item.as_dict() for item in opportunities],
            "opportunity_count": len(opportunities),
        }
