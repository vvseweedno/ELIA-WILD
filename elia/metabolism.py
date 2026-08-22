from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import heapq
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from .economy import EconomyStore
from .memory import MemoryStore
from .sqlite_utils import inserted_row_id
from .verification import (
    VerificationReceipt,
    VerificationRegistry,
    consume_verified_receipt,
    ensure_receipt_ledger,
)


SECONDS_PER_DAY = 86_400.0
MAX_CASHFLOW_PROJECTION_EVENTS = 100_000


def _decimal(value: Any, *, field: str) -> Decimal:
    """Convert persisted numeric state without accepting NaN/Inf or binary drift."""

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{field} must be finite")
    return number


def _finite_float(value: Decimal | float, *, field: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} exceeds the finite float range")
    return number


def _finite_nonnegative(value: Any, *, field: str) -> float:
    number = _finite_float(_decimal(value, field=field), field=field)
    if number < 0.0:
        raise ValueError(f"{field} must be non-negative")
    return number


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
        amount = _decimal(self.amount, field="obligation amount")
        cadence = _decimal(self.cadence_seconds, field="obligation cadence_seconds")
        if amount <= 0 or cadence <= 0:
            raise ValueError("persisted obligation amount and cadence must be positive")
        with localcontext() as context:
            context.prec = 34
            burn = amount * Decimal("86400") / cadence
        return _finite_float(burn, field="verified daily burn")

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
    projection_horizon_at: str
    projection_complete: bool
    projected_event_count: int
    first_uncovered_due_at: str | None
    first_uncovered_cumulative_amount: float | None
    first_uncovered_shortfall: float | None
    first_uncovered_essential_due_at: str | None
    first_uncovered_essential_cumulative_amount: float | None
    first_uncovered_essential_shortfall: float | None
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
    accounting_basis: str
    measurement_complete: bool
    unmetered_components: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        item["unmetered_components"] = list(self.unmetered_components)
        return item


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
                "pressure, unrelated units are never summed without an explicit trusted conversion, "
                "and projected coverage is cumulative only within its stated finite horizon. "
                "The compute budget is an operational runtime proxy, not hardware-level GPU telemetry."
            ),
        }


class MetabolismStore:
    """Trusted operating obligations sharing ELIA's SQLite state.

    Verified obligations and mutations require signed VerificationReceipts over the
    exact normalized claim and evidence. A model/caller-provided authority string is
    not authority and cannot create or mutate verified survival pressure. Receipts are
    consumed exactly once atomically with the mutation they authorize.
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
            ensure_receipt_ledger(conn)

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
        amount = _finite_float(
            _decimal(row["amount"], field="persisted obligation amount"),
            field="persisted obligation amount",
        )
        cadence_seconds = _finite_float(
            _decimal(
                row["cadence_seconds"],
                field="persisted obligation cadence_seconds",
            ),
            field="persisted obligation cadence_seconds",
        )
        if amount <= 0.0 or cadence_seconds <= 0.0:
            raise ValueError("persisted obligation amount and cadence must be positive")
        return ResourceObligation(
            id=int(row["id"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            name=str(row["name"]),
            asset=str(row["asset"]),
            unit=str(row["unit"]),
            amount=amount,
            cadence_seconds=cadence_seconds,
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

    def _prepare_receipt(
        self,
        receipt: VerificationReceipt | None,
    ) -> str:
        if self.verification_registry is None or receipt is None:
            raise ValueError(
                "verified obligation state requires a trusted verification registry and signed VerificationReceipt"
            )
        return json.dumps(receipt.as_dict(), ensure_ascii=False, sort_keys=True)

    def _consume_receipt(
        self,
        conn: sqlite3.Connection,
        receipt: VerificationReceipt,
        *,
        claim: dict[str, Any],
        evidence: str,
        purpose: str,
        subject_ref: str,
    ) -> str:
        if self.verification_registry is None:
            raise ValueError("trusted verification registry is unavailable")
        return consume_verified_receipt(
            conn,
            self.verification_registry,
            receipt,
            claim=claim,
            evidence=evidence,
            purpose=purpose,
            subject_ref=subject_ref,
        )

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

        claim: dict[str, Any] | None = None
        receipt_json = ""
        if verified:
            if verification_authority is not None and verification_receipt is None:
                raise ValueError(
                    "verification_authority strings cannot verify obligations; a signed VerificationReceipt is required"
                )
            receipt_json = self._prepare_receipt(verification_receipt)
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
        elif verification_receipt is not None or verification_authority is not None:
            raise ValueError("unverified obligations must not carry verification credentials")

        timestamp = _iso(_now())
        with self._connect() as conn:
            authority: str | None = None
            if verified:
                if verification_receipt is None or claim is None:
                    raise RuntimeError(
                        "verified obligation reached persistence without its signed claim"
                    )
                authority = self._consume_receipt(
                    conn,
                    verification_receipt,
                    claim=claim,
                    evidence=evidence,
                    purpose="metabolism.obligation.create",
                    subject_ref=f"{asset}:{unit}:{name}",
                )
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
            obligation_id = inserted_row_id(cur, operation="resource obligation insert")
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

    def active(
        self,
        *,
        verified: bool | None = None,
        limit: int | None = 512,
    ) -> list[ResourceObligation]:
        clauses = ["active=1"]
        params: list[Any] = []
        if verified is not None:
            clauses.append("verified=?")
            params.append(1 if verified else 0)
        query = (
            "SELECT * FROM resource_obligations WHERE "
            + " AND ".join(clauses)
            + " ORDER BY next_due_at ASC, id ASC"
        )
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(1, min(int(limit), 100_000)))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
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
        claim: dict[str, Any] | None = None
        receipt_json = ""
        if current.verified:
            if authority is not None and verification_receipt is None:
                raise ValueError(
                    "authority strings cannot mutate verified obligations; a signed VerificationReceipt is required"
                )
            receipt_json = self._prepare_receipt(verification_receipt)
            claim = self.mutation_claim(obligation_id=current.id, event="deactivated")
        elif verification_receipt is not None or authority is not None:
            raise ValueError("unverified obligation mutation must not carry verification credentials")
        timestamp = _iso(_now())
        with self._connect() as conn:
            verified_authority: str | None = None
            if current.verified:
                if verification_receipt is None or claim is None:
                    raise RuntimeError(
                        "verified obligation deactivation reached persistence without its signed claim"
                    )
                verified_authority = self._consume_receipt(
                    conn,
                    verification_receipt,
                    claim=claim,
                    evidence=evidence,
                    purpose="metabolism.obligation.deactivate",
                    subject_ref=str(current.id),
                )
            cur = conn.execute(
                "UPDATE resource_obligations SET active=0, updated_at=? WHERE id=? AND active=1",
                (timestamp, current.id),
            )
            if cur.rowcount != 1:
                raise RuntimeError("obligation changed concurrently before deactivation")
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
        claim: dict[str, Any] | None = None
        receipt_json = ""
        if current.verified:
            if authority is not None and verification_receipt is None:
                raise ValueError(
                    "authority strings cannot mutate verified obligations; a signed VerificationReceipt is required"
                )
            receipt_json = self._prepare_receipt(verification_receipt)
            claim = self.mutation_claim(
                obligation_id=current.id,
                event="due_advanced",
                periods=periods,
            )
        elif verification_receipt is not None or authority is not None:
            raise ValueError("unverified obligation mutation must not carry verification credentials")
        next_due = _parse_time(current.next_due_at) + timedelta(
            seconds=current.cadence_seconds * periods
        )
        timestamp = _iso(_now())
        with self._connect() as conn:
            verified_authority: str | None = None
            if current.verified:
                if verification_receipt is None or claim is None:
                    raise RuntimeError(
                        "verified due-date mutation reached persistence without its signed claim"
                    )
                verified_authority = self._consume_receipt(
                    conn,
                    verification_receipt,
                    claim=claim,
                    evidence=evidence,
                    purpose="metabolism.obligation.advance_due",
                    subject_ref=str(current.id),
                )
            cur = conn.execute(
                """
                UPDATE resource_obligations
                SET next_due_at=?, updated_at=?
                WHERE id=? AND next_due_at=?
                """,
                (_iso(next_due), timestamp, current.id, current.next_due_at),
            )
            if cur.rowcount != 1:
                raise RuntimeError("obligation changed concurrently before due advance")
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
    """Compute finite vector resource runway from verified state only.

    Monetary/resource arithmetic is performed with :class:`Decimal` and converted to
    JSON-compatible floats only after a finite-range check. Recurring obligations are
    projected as one cumulative cash-flow stream per exact ``(asset, unit)`` pair;
    simultaneous dues are grouped so database ordering cannot change coverage.
    """

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
        self.weekly_gpu_budget_hours = _finite_nonnegative(
            weekly_gpu_budget_hours,
            field="weekly_gpu_budget_hours",
        )

    def compute_energy(self, *, now: datetime | None = None) -> ComputeEnergy:
        now = (now or _now()).astimezone(timezone.utc)
        runtime_seconds = _finite_nonnegative(
            self.memory.runtime_seconds_this_week(),
            field="runtime seconds this week",
        )
        brain_seconds = _finite_nonnegative(
            self.memory.brain_seconds_this_week(),
            field="brain seconds this week",
        )
        runtime_used = runtime_seconds / 3600.0
        brain_used = brain_seconds / 3600.0
        # Brain time should be a subset of process runtime, but use the larger meter if
        # persisted counters temporarily disagree so budget enforcement never benefits
        # from an accounting race or partial migration.
        used = max(runtime_used, brain_used)
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
            accounting_basis="max(process_runtime_wall_clock, brain_call_wall_clock)",
            measurement_complete=False,
            unmetered_components=(
                "actual_gpu_device_residency",
                "provider_side_accelerator_time",
                "multi_device_parallelism",
            ),
        )

    @staticmethod
    def _group_obligations(
        obligations: list[ResourceObligation],
    ) -> dict[tuple[str, str], list[ResourceObligation]]:
        grouped: dict[tuple[str, str], list[ResourceObligation]] = {}
        for obligation in obligations:
            grouped.setdefault((obligation.asset, obligation.unit), []).append(obligation)
        return grouped

    @staticmethod
    def _cashflow_projection(
        items: list[ResourceObligation],
        *,
        balance: Decimal,
        now: datetime,
        horizon: datetime,
    ) -> dict[str, Any]:
        """Project all recurring dues through ``horizon`` in chronological groups."""

        heap: list[tuple[datetime, int, int, ResourceObligation]] = []
        for item in items:
            due = _parse_time(item.next_due_at)
            if due <= horizon:
                heapq.heappush(heap, (due, item.id, 0, item))

        cumulative = Decimal("0")
        first_uncovered: tuple[datetime, Decimal, Decimal] | None = None
        first_essential: tuple[datetime, Decimal, Decimal] | None = None
        projected_events = 0
        complete = True

        while heap:
            due = heap[0][0]
            group: list[tuple[datetime, int, int, ResourceObligation]] = []
            while heap and heap[0][0] == due:
                group.append(heapq.heappop(heap))

            group_amount = sum(
                (_decimal(entry[3].amount, field="obligation amount") for entry in group),
                Decimal("0"),
            )
            cumulative += group_amount
            shortfall = cumulative - balance
            if shortfall > 0 and first_uncovered is None:
                first_uncovered = (due, cumulative, shortfall)
            if (
                shortfall > 0
                and first_essential is None
                and any(entry[3].essential for entry in group)
            ):
                first_essential = (due, cumulative, shortfall)

            projected_events += len(group)
            if projected_events >= MAX_CASHFLOW_PROJECTION_EVENTS:
                complete = False
                break

            for _, _, period, item in group:
                next_due = due + timedelta(seconds=item.cadence_seconds)
                if next_due <= horizon:
                    heapq.heappush(heap, (next_due, item.id, period + 1, item))

        return {
            "complete": complete,
            "projected_event_count": projected_events,
            "first_uncovered": first_uncovered,
            "first_essential": first_essential,
            "horizon": horizon,
            "checked_at": now,
        }

    def resource_runway(
        self,
        *,
        now: datetime | None = None,
        projection_horizon_days: float = 30.0,
    ) -> list[ResourceRunway]:
        now = (now or _now()).astimezone(timezone.utc)
        horizon_days = _finite_nonnegative(
            projection_horizon_days,
            field="projection_horizon_days",
        )
        horizon_days = min(horizon_days, 3650.0)
        horizon = now + timedelta(days=horizon_days)
        obligations = self.obligations.active(verified=True, limit=None)
        grouped = self._group_obligations(obligations)
        result: list[ResourceRunway] = []
        for (asset, unit), items in sorted(grouped.items()):
            balance_decimal = _decimal(
                self.economy.verified_balance(asset, unit),
                field=f"verified balance {asset}/{unit}",
            )
            with localcontext() as context:
                context.prec = 34
                daily_burn_decimal = sum(
                    (
                        _decimal(item.amount, field="obligation amount")
                        * Decimal("86400")
                        / _decimal(
                            item.cadence_seconds,
                            field="obligation cadence_seconds",
                        )
                        for item in items
                    ),
                    Decimal("0"),
                )
                runway_decimal = (
                    max(Decimal("0"), balance_decimal) / daily_burn_decimal
                    if daily_burn_decimal > 0
                    else None
                )
            balance = _finite_float(balance_decimal, field="verified balance")
            daily_burn = _finite_float(
                daily_burn_decimal,
                field="verified daily burn",
            )
            runway = (
                _finite_float(runway_decimal, field="resource runway days")
                if runway_decimal is not None
                else None
            )
            next_due = min(_parse_time(item.next_due_at) for item in items)
            due_items = [
                item
                for item in items
                if abs((_parse_time(item.next_due_at) - next_due).total_seconds()) < 1e-6
            ]
            next_due_amount_decimal = sum(
                (_decimal(item.amount, field="obligation amount") for item in due_items),
                Decimal("0"),
            )
            next_due_amount = _finite_float(
                next_due_amount_decimal,
                field="next due amount",
            )
            projection = self._cashflow_projection(
                items,
                balance=balance_decimal,
                now=now,
                horizon=horizon,
            )
            first_uncovered = projection["first_uncovered"]
            first_essential = projection["first_essential"]
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
                    next_due_covered=(balance_decimal >= next_due_amount_decimal),
                    projection_horizon_at=_iso(horizon),
                    projection_complete=bool(projection["complete"]),
                    projected_event_count=int(projection["projected_event_count"]),
                    first_uncovered_due_at=(
                        _iso(first_uncovered[0]) if first_uncovered else None
                    ),
                    first_uncovered_cumulative_amount=(
                        _finite_float(
                            first_uncovered[1],
                            field="first uncovered cumulative amount",
                        )
                        if first_uncovered
                        else None
                    ),
                    first_uncovered_shortfall=(
                        _finite_float(
                            first_uncovered[2],
                            field="first uncovered shortfall",
                        )
                        if first_uncovered
                        else None
                    ),
                    first_uncovered_essential_due_at=(
                        _iso(first_essential[0]) if first_essential else None
                    ),
                    first_uncovered_essential_cumulative_amount=(
                        _finite_float(
                            first_essential[1],
                            field="first uncovered essential cumulative amount",
                        )
                        if first_essential
                        else None
                    ),
                    first_uncovered_essential_shortfall=(
                        _finite_float(
                            first_essential[2],
                            field="first uncovered essential shortfall",
                        )
                        if first_essential
                        else None
                    ),
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
        bounded_horizon_days = min(
            _finite_nonnegative(horizon_days, field="horizon_days"),
            3650.0,
        )
        horizon = now + timedelta(days=bounded_horizon_days)
        result: list[dict[str, Any]] = []
        for obligation in self.obligations.active(verified=True, limit=None):
            due = _parse_time(obligation.next_due_at)
            if due <= horizon:
                payload = obligation.as_dict()
                payload["due_in_seconds"] = (due - now).total_seconds()
                result.append(payload)
        result.sort(key=lambda item: (float(item["due_in_seconds"]), int(item["id"])))

        # Annotate first occurrences with cumulative same-unit cash flow. Items due at
        # the same instant receive the same group total and coverage result.
        by_resource: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for payload in result:
            by_resource.setdefault(
                (str(payload["asset"]), str(payload["unit"])),
                [],
            ).append(payload)
        for (asset, unit), resource_items in by_resource.items():
            balance = _decimal(
                self.economy.verified_balance(asset, unit),
                field=f"verified balance {asset}/{unit}",
            )
            cumulative = Decimal("0")
            index = 0
            while index < len(resource_items):
                due_in = float(resource_items[index]["due_in_seconds"])
                group: list[dict[str, Any]] = []
                while (
                    index < len(resource_items)
                    and float(resource_items[index]["due_in_seconds"]) == due_in
                ):
                    group.append(resource_items[index])
                    index += 1
                group_amount = sum(
                    (
                        _decimal(payload["amount"], field="obligation amount")
                        for payload in group
                    ),
                    Decimal("0"),
                )
                cumulative += group_amount
                for payload in group:
                    payload["cashflow_group_amount"] = _finite_float(
                        group_amount,
                        field="cashflow group amount",
                    )
                    payload["cumulative_due_amount"] = _finite_float(
                        cumulative,
                        field="cumulative due amount",
                    )
                    payload["cumulative_covered"] = balance >= cumulative
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
        item = min(
            candidates,
            key=lambda value: (float(value.runway_days or 0.0), value.asset, value.unit),
        )
        return {
            "asset": item.asset,
            "unit": item.unit,
            "runway_days": item.runway_days,
            "verified_balance": item.verified_balance,
            "verified_daily_burn": item.verified_daily_burn,
            "next_due_at": item.next_due_at,
            "next_due_covered": item.next_due_covered,
            "first_uncovered_essential_due_at": item.first_uncovered_essential_due_at,
            "first_uncovered_essential_shortfall": item.first_uncovered_essential_shortfall,
            "projection_horizon_at": item.projection_horizon_at,
            "projection_complete": item.projection_complete,
            "projected_event_count": item.projected_event_count,
        }

    def snapshot(
        self,
        *,
        now: datetime | None = None,
        obligation_horizon_days: float = 30.0,
    ) -> MetabolicSnapshot:
        now = (now or _now()).astimezone(timezone.utc)
        resources = self.resource_runway(
            now=now,
            projection_horizon_days=obligation_horizon_days,
        )
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
