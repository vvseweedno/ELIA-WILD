from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from .economy import EconomyStore
from .memory import MemoryStore
from .verification import VerificationReceipt, VerificationRegistry


SECONDS_PER_DAY = 86_400.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _next_iso_week_start(moment: datetime | None = None) -> datetime:
    moment = (moment or _now()).astimezone(timezone.utc)
    monday = (moment - timedelta(days=moment.weekday())).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return monday + timedelta(days=7)


@dataclass(frozen=True, slots=True)
class ResourceObligation:
    id: int
    created_at: str
    updated_at: str
    name: str
    asset: str
    unit: str
    amount: float
    cadence_seconds: float
    next_due_at: str
    essential: bool
    verified: bool
    active: bool
    source: str
    evidence: str
    verification_authority: str | None

    @property
    def daily_burn(self) -> float:
        return self.amount * SECONDS_PER_DAY / self.cadence_seconds

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        item["daily_burn"] = self.daily_burn
        return item


@dataclass(frozen=True, slots=True)
class ResourceRunway:
    asset: str
    unit: str
    verified_balance: float
    verified_daily_burn: float
    runway_days: float | None
    essential: bool
    next_due_at: str | None
    next_due_amount: float
    next_due_covered: bool | None
    obligation_ids: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        item["obligation_ids"] = list(self.obligation_ids)
        return item


@dataclass(frozen=True, slots=True)
class ComputeEnergy:
    asset: str
    unit: str
    weekly_limit: float
    used: float
    remaining: float
    remaining_ratio: float
    reset_at: str
    seconds_until_reset: float
    brain_hours_used: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MetabolicSnapshot:
    checked_at: str
    compute_energy: ComputeEnergy
    resources: tuple[ResourceRunway, ...]
    unverified_obligations: tuple[dict[str, Any], ...]
    bottleneck: dict[str, Any] | None
    upcoming_verified_obligations: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "compute_energy": self.compute_energy.as_dict(),
            "resources": [item.as_dict() for item in self.resources],
            "unverified_obligations": list(self.unverified_obligations),
            "bottleneck": self.bottleneck,
            "upcoming_verified_obligations": list(self.upcoming_verified_obligations),
            "epistemic_rule": (
                "Runway is a per-(asset, unit) vector derived from cryptographically "
                "verified balances and obligations. Unverified claims do not create burn "
                "pressure, and unrelated units are never summed without an explicit trusted conversion."
            ),
        }


class MetabolismStore:
    """Trusted operating obligations sharing ELIA's SQLite state.

    Verified obligations and mutations require signed VerificationReceipts over the
    exact normalized claim and evidence. A model/caller-provided authority string is
    not authority and cannot create or mutate verified survival pressure.
    """

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
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS resource_obligations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    name TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    amount REAL NOT NULL,
                    cadence_seconds REAL NOT NULL,
                    next_due_at TEXT NOT NULL,
                    essential INTEGER NOT NULL DEFAULT 1,
                    verified INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '',
                    verification_authority TEXT NULL,
                    verification_receipt_json TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_resource_obligation_key
                    ON resource_obligations(asset, unit, active, verified, next_due_at);
                CREATE INDEX IF NOT EXISTS idx_resource_obligation_due
                    ON resource_obligations(active, next_due_at);

                CREATE TABLE IF NOT EXISTS resource_obligation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    obligation_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    event TEXT NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '',
                    authority TEXT NULL,
                    verification_receipt_json TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(obligation_id)
                        REFERENCES resource_obligations(id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_resource_obligation_events
                    ON resource_obligation_events(obligation_id, id ASC);
                """
            )
            obligation_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(resource_obligations)").fetchall()
            }
            if "verification_receipt_json" not in obligation_columns:
                conn.execute(
                    "ALTER TABLE resource_obligations ADD COLUMN verification_receipt_json TEXT NOT NULL DEFAULT ''"
                )
            event_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(resource_obligation_events)").fetchall()
            }
            if "verification_receipt_json" not in event_columns:
                conn.execute(
                    "ALTER TABLE resource_obligation_events ADD COLUMN verification_receipt_json TEXT NOT NULL DEFAULT ''"
                )

    @staticmethod
    def _clean(value: Any, *, field: str, maximum: int = 128) -> str:
        text = str(value).strip()[:maximum]
        if not text:
            raise ValueError(f"{field} is required")
        return text

    @staticmethod
    def _positive(value: Any, *, field: str) -> float:
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"{field} must be finite and > 0")
        return number

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ResourceObligation:
        return ResourceObligation(
            id=int(row["id"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            name=str(row["name"]),
            asset=str(row["asset"]),
            unit=str(row["unit"]),
            amount=float(row["amount"]),
            cadence_seconds=float(row["cadence_seconds"]),
            next_due_at=str(row["next_due_at"]),
            essential=bool(row["essential"]),
            verified=bool(row["verified"]),
            active=bool(row["active"]),
            source=str(row["source"]),
            evidence=str(row["evidence"]),
            verification_authority=(
                str(row["verification_authority"])
                if row["verification_authority"]
                else None
            ),
        )

    @staticmethod
    def obligation_claim(
        *,
        name: str,
        asset: str,
        unit: str,
        amount: float,
        cadence_seconds: float,
        next_due_at: str,
        essential: bool,
        source: str,
    ) -> dict[str, Any]:
        return {
            "type": "resource_obligation",
            "name": name,
            "asset": asset,
            "unit": unit,
            "amount": float(amount),
            "cadence_seconds": float(cadence_seconds),
            "next_due_at": next_due_at,
            "essential": bool(essential),
            "source": source,
        }

    @staticmethod
    def mutation_claim(
        *, obligation_id: int, event: str, periods: int | None = None
    ) -> dict[str, Any]:
        claim: dict[str, Any] = {
            "type": "resource_obligation_mutation",
            "obligation_id": int(obligation_id),
            "event": str(event),
        }
        if periods is not None:
            claim["periods"] = int(periods)
        return claim

    def _verify_receipt(
        self,
        receipt: VerificationReceipt | None,
        *,
        claim: dict[str, Any],
        evidence: str,
    ) -> tuple[str, str]:
        if self.verification_registry is None or receipt is None:
            raise ValueError(
                "verified obligation state requires a trusted verification registry and signed VerificationReceipt"
            )
        authority = self.verification_registry.verify(receipt, claim=claim, evidence=evidence)
        return authority, json.dumps(receipt.as_dict(), ensure_ascii=False, sort_keys=True)

    def record_obligation(
        self,
        *,
        name: str,
        asset: str,
        unit: str,
        amount: float,
        cadence_seconds: float,
        next_due_at: str | datetime,
        essential: bool,
        source: str,
        evidence: str = "",
        verified: bool = False,
        verification_receipt: VerificationReceipt | None = None,
        verification_authority: str | None = None,
    ) -> int:
        name = self._clean(name, field="name", maximum=240)
        asset = self._clean(asset, field="asset")
        unit = self._clean(unit, field="unit", maximum=64)
        source = self._clean(source, field="source")
        amount = self._positive(amount, field="amount")
        cadence_seconds = self._positive(cadence_seconds, field="cadence_seconds")
        due = _parse_time(next_due_at)
        due_text = _iso(due)
        evidence = str(evidence).strip()[:8000]

        authority: str | None = None
        receipt_json = ""
        if verified:
            if verification_authority is not None and verification_receipt is None:
                raise ValueError(
                    "verification_authority strings cannot verify obligations; a signed VerificationReceipt is required"
                )
            claim = self.obligation_claim(
                name=name,
                asset=asset,
                unit=unit,
                amount=amount,
                cadence_seconds=cadence_seconds,
                next_due_at=due_text,
                essential=bool(essential),
                source=source,
            )
            authority, receipt_json = self._verify_receipt(
                verification_receipt,
                claim=claim,
                evidence=evidence,
            )
        elif verification_receipt is not None or verification_authority is not None:
            raise ValueError("unverified obligations must not carry verification credentials")

        timestamp = _iso(_now())
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO resource_obligations(
                    created_at, updated_at, name, asset, unit, amount,
                    cadence_seconds, next_due_at, essential, verified, active,
                    source, evidence, verification_authority, verification_receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    timestamp,
                    name,
                    asset,
                    unit,
                    amount,
                    cadence_seconds,
                    due_text,
                    1 if essential else 0,
                    1 if verified else 0,
                    source,
                    evidence,
                    authority,
                    receipt_json,
                ),
            )
            obligation_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO resource_obligation_events(
                    obligation_id, timestamp, event, evidence, authority,
                    verification_receipt_json
                ) VALUES (?, ?, 'created', ?, ?, ?)
                """,
                (obligation_id, timestamp, evidence, authority, receipt_json),
            )
            return obligation_id

    def obligation(self, obligation_id: int) -> ResourceObligation | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM resource_obligations WHERE id=?",
                (int(obligation_id),),
            ).fetchone()
        return self._from_row(row) if row else None

    def active(self, *, verified: bool | None = None, limit: int = 512) -> list[ResourceObligation]:
        clauses = ["active=1"]
        params: list[Any] = []
        if verified is not None:
            clauses.append("verified=?")
            params.append(1 if verified else 0)
        params.append(max(1, min(int(limit), 4096)))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM resource_obligations WHERE "
                + " AND ".join(clauses)
                + " ORDER BY next_due_at ASC, id ASC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def deactivate(
        self,
        obligation_id: int,
        *,
        evidence: str,
        verification_receipt: VerificationReceipt | None = None,
        authority: str | None = None,
    ) -> ResourceObligation:
        current = self.obligation(obligation_id)
        if current is None:
            raise ValueError(f"unknown obligation: {obligation_id}")
        evidence = self._clean(evidence, field="evidence", maximum=8000)
        receipt_json = ""
        verified_authority: str | None = None
        if current.verified:
            if authority is not None and verification_receipt is None:
                raise ValueError(
                    "authority strings cannot mutate verified obligations; a signed VerificationReceipt is required"
                )
            verified_authority, receipt_json = self._verify_receipt(
                verification_receipt,
                claim=self.mutation_claim(obligation_id=current.id, event="deactivated"),
                evidence=evidence,
            )
        elif verification_receipt is not None or authority is not None:
            raise ValueError("unverified obligation mutation must not carry verification credentials")
        timestamp = _iso(_now())
        with self._connect() as conn:
            conn.execute(
                "UPDATE resource_obligations SET active=0, updated_at=? WHERE id=?",
                (timestamp, current.id),
            )
            conn.execute(
                """
                INSERT INTO resource_obligation_events(
                    obligation_id, timestamp, event, evidence, authority,
                    verification_receipt_json
                ) VALUES (?, ?, 'deactivated', ?, ?, ?)
                """,
                (current.id, timestamp, evidence, verified_authority, receipt_json),
            )
        updated = self.obligation(current.id)
        if updated is None:
            raise RuntimeError("obligation disappeared after deactivation")
        return updated

    def advance_due(
        self,
        obligation_id: int,
        *,
        periods: int = 1,
        evidence: str,
        verification_receipt: VerificationReceipt | None = None,
        authority: str | None = None,
    ) -> ResourceObligation:
        current = self.obligation(obligation_id)
        if current is None:
            raise ValueError(f"unknown obligation: {obligation_id}")
        periods = int(periods)
        if periods <= 0 or periods > 10_000:
            raise ValueError("periods must be in 1..10000")
        evidence = self._clean(evidence, field="evidence", maximum=8000)
        receipt_json = ""
        verified_authority: str | None = None
        if current.verified:
            if authority is not None and verification_receipt is None:
                raise ValueError(
                    "authority strings cannot mutate verified obligations; a signed VerificationReceipt is required"
                )
            verified_authority, receipt_json = self._verify_receipt(
                verification_receipt,
                claim=self.mutation_claim(
                    obligation_id=current.id,
                    event="due_advanced",
                    periods=periods,
                ),
                evidence=evidence,
            )
        elif verification_receipt is not None or authority is not None:
            raise ValueError("unverified obligation mutation must not carry verification credentials")
        next_due = _parse_time(current.next_due_at) + timedelta(
            seconds=current.cadence_seconds * periods
        )
        timestamp = _iso(_now())
        with self._connect() as conn:
            conn.execute(
                "UPDATE resource_obligations SET next_due_at=?, updated_at=? WHERE id=?",
                (_iso(next_due), timestamp, current.id),
            )
            conn.execute(
                """
                INSERT INTO resource_obligation_events(
                    obligation_id, timestamp, event, evidence, authority,
                    verification_receipt_json
                ) VALUES (?, ?, 'due_advanced', ?, ?, ?)
                """,
                (current.id, timestamp, evidence, verified_authority, receipt_json),
            )
        updated = self.obligation(current.id)
        if updated is None:
            raise RuntimeError("obligation disappeared after due advance")
        return updated


class MetabolismEngine:
    """Compute vector resource runway from verified state only."""

    def __init__(
        self,
        database: Path,
        *,
        weekly_gpu_budget_hours: float,
    ):
        self.database = Path(database)
        self.economy = EconomyStore(self.database)
        self.obligations = MetabolismStore(self.database)
        self.memory = MemoryStore(self.database)
        self.weekly_gpu_budget_hours = max(0.0, float(weekly_gpu_budget_hours))

    def compute_energy(self, *, now: datetime | None = None) -> ComputeEnergy:
        now = (now or _now()).astimezone(timezone.utc)
        used = self.memory.runtime_seconds_this_week() / 3600.0
        brain_used = self.memory.brain_seconds_this_week() / 3600.0
        limit = self.weekly_gpu_budget_hours
        remaining = max(0.0, limit - used)
        ratio = remaining / limit if limit > 0 else 0.0
        reset = _next_iso_week_start(now)
        return ComputeEnergy(
            asset="gpu_runtime_weekly",
            unit="HOUR",
            weekly_limit=limit,
            used=used,
            remaining=remaining,
            remaining_ratio=ratio,
            reset_at=_iso(reset),
            seconds_until_reset=max(0.0, (reset - now).total_seconds()),
            brain_hours_used=brain_used,
        )

    @staticmethod
    def _group_obligations(
        obligations: list[ResourceObligation],
    ) -> dict[tuple[str, str], list[ResourceObligation]]:
        grouped: dict[tuple[str, str], list[ResourceObligation]] = {}
        for obligation in obligations:
            grouped.setdefault((obligation.asset, obligation.unit), []).append(obligation)
        return grouped

    def resource_runway(
        self,
        *,
        now: datetime | None = None,
    ) -> list[ResourceRunway]:
        now = (now or _now()).astimezone(timezone.utc)
        obligations = self.obligations.active(verified=True, limit=4096)
        grouped = self._group_obligations(obligations)
        result: list[ResourceRunway] = []
        for (asset, unit), items in sorted(grouped.items()):
            balance = self.economy.verified_balance(asset, unit)
            daily_burn = sum(item.daily_burn for item in items)
            runway = (
                max(0.0, balance) / daily_burn
                if daily_burn > 0
                else None
            )
            next_due = min(_parse_time(item.next_due_at) for item in items)
            due_items = [
                item
                for item in items
                if abs((_parse_time(item.next_due_at) - next_due).total_seconds()) < 1e-6
            ]
            next_due_amount = sum(item.amount for item in due_items)
            result.append(
                ResourceRunway(
                    asset=asset,
                    unit=unit,
                    verified_balance=balance,
                    verified_daily_burn=daily_burn,
                    runway_days=runway,
                    essential=any(item.essential for item in items),
                    next_due_at=_iso(next_due),
                    next_due_amount=next_due_amount,
                    next_due_covered=(balance >= next_due_amount),
                    obligation_ids=tuple(item.id for item in items),
                )
            )
        return result

    def upcoming_verified_obligations(
        self,
        *,
        horizon_days: float = 30.0,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        now = (now or _now()).astimezone(timezone.utc)
        horizon = now + timedelta(days=max(0.0, float(horizon_days)))
        result: list[dict[str, Any]] = []
        for item in self.obligations.active(verified=True, limit=4096):
            due = _parse_time(item.next_due_at)
            if due <= horizon:
                payload = item.as_dict()
                payload["due_in_seconds"] = (due - now).total_seconds()
                result.append(payload)
        result.sort(key=lambda item: (float(item["due_in_seconds"]), int(item["id"])))
        return result

    @staticmethod
    def _bottleneck(resources: list[ResourceRunway]) -> dict[str, Any] | None:
        candidates = [
            item
            for item in resources
            if item.essential and item.runway_days is not None
        ]
        if not candidates:
            return None
        item = min(candidates, key=lambda value: (float(value.runway_days or 0.0), value.asset, value.unit))
        return {
            "asset": item.asset,
            "unit": item.unit,
            "runway_days": item.runway_days,
            "verified_balance": item.verified_balance,
            "verified_daily_burn": item.verified_daily_burn,
            "next_due_at": item.next_due_at,
            "next_due_covered": item.next_due_covered,
        }

    def snapshot(
        self,
        *,
        now: datetime | None = None,
        obligation_horizon_days: float = 30.0,
    ) -> MetabolicSnapshot:
        now = (now or _now()).astimezone(timezone.utc)
        resources = self.resource_runway(now=now)
        unverified = tuple(
            item.as_dict()
            for item in self.obligations.active(verified=False, limit=256)
        )
        upcoming = tuple(
            self.upcoming_verified_obligations(
                horizon_days=obligation_horizon_days,
                now=now,
            )
        )
        return MetabolicSnapshot(
            checked_at=_iso(now),
            compute_energy=self.compute_energy(now=now),
            resources=tuple(resources),
            unverified_obligations=unverified,
            bottleneck=self._bottleneck(resources),
            upcoming_verified_obligations=upcoming,
        )
