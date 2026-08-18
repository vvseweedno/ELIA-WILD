from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from .economy import EconomyStore, Opportunity


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any, *, field: str, maximum: int = 128) -> str:
    text = str(value).strip()[:maximum]
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _finite_nonnegative(value: Any, *, field: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return number


def _probability(value: Any, *, field: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return max(0.0, min(1.0, number))


@dataclass(frozen=True, slots=True)
class ResourceProfile:
    opportunity_id: int
    updated_at: str
    target_asset: str
    target_unit: str
    target_amount: float
    eligibility_confidence: float
    evidence_quality: float
    evidence: str
    blockers: tuple[str, ...]
    source: str

    @property
    def qualification_score(self) -> float:
        return self.eligibility_confidence * self.evidence_quality

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        item["blockers"] = list(self.blockers)
        item["qualification_score"] = self.qualification_score
        item["epistemic_status"] = "estimated_not_verified"
        return item


@dataclass(frozen=True, slots=True)
class WorkItem:
    id: int
    opportunity_id: int
    created_at: str
    updated_at: str
    status: str
    objective: str
    deliverable_spec: str
    acceptance_criteria: str
    estimated_gpu_hours: float
    artifact_path: str | None
    submission_observation_id: int | None
    external_evidence: str
    resource_event_id: int | None
    source: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ResourceCandidate:
    opportunity: Opportunity
    profile: ResourceProfile
    bottleneck_match: bool
    expected_resource_amount: float
    expected_resource_per_gpu_hour: float
    expected_runway_gain_days: float | None
    work_items: tuple[WorkItem, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity": self.opportunity.as_dict(),
            "resource_profile": self.profile.as_dict(),
            "bottleneck_match": self.bottleneck_match,
            "expected_resource_amount": self.expected_resource_amount,
            "expected_resource_per_gpu_hour": self.expected_resource_per_gpu_hour,
            "expected_runway_gain_days": self.expected_runway_gain_days,
            "work_items": [item.as_dict() for item in self.work_items],
        }


class ResourceEcologyStore:
    """Local opportunity/resource alignment and work lifecycle state.

    Resource profiles are estimates, never receipts. Work plans are executable intent,
    never evidence of delivery. A work item becomes `realized` only when it is linked
    to an already-verified positive resource_event with an exact matching
    `(asset, unit)` resource profile.
    """

    WORK_STATUSES = {
        "planned",
        "staged",
        "submitted",
        "accepted",
        "rejected",
        "abandoned",
        "realized",
    }
    TERMINAL_WORK_STATUSES = {"rejected", "abandoned", "realized"}

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
                CREATE TABLE IF NOT EXISTS opportunity_resource_profiles (
                    opportunity_id INTEGER PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    target_asset TEXT NOT NULL,
                    target_unit TEXT NOT NULL,
                    target_amount REAL NOT NULL,
                    eligibility_confidence REAL NOT NULL,
                    evidence_quality REAL NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '',
                    blockers_json TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL DEFAULT 'brain',
                    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_resource_profiles_key
                    ON opportunity_resource_profiles(target_asset, target_unit, opportunity_id);

                CREATE TABLE IF NOT EXISTS ecology_work_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    opportunity_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'planned',
                    objective TEXT NOT NULL,
                    deliverable_spec TEXT NOT NULL,
                    acceptance_criteria TEXT NOT NULL,
                    estimated_gpu_hours REAL NOT NULL DEFAULT 0,
                    artifact_path TEXT NULL,
                    submission_observation_id INTEGER NULL,
                    external_evidence TEXT NOT NULL DEFAULT '',
                    resource_event_id INTEGER NULL,
                    source TEXT NOT NULL DEFAULT 'brain',
                    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE,
                    FOREIGN KEY(resource_event_id) REFERENCES resource_events(id)
                );
                CREATE INDEX IF NOT EXISTS idx_ecology_work_opportunity
                    ON ecology_work_items(opportunity_id, status, id ASC);

                CREATE TABLE IF NOT EXISTS ecology_work_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    work_item_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    event TEXT NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '',
                    observation_id INTEGER NULL,
                    resource_event_id INTEGER NULL,
                    FOREIGN KEY(work_item_id) REFERENCES ecology_work_items(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_ecology_work_events
                    ON ecology_work_events(work_item_id, id ASC);
                """
            )

    @staticmethod
    def _profile_from_row(row: sqlite3.Row) -> ResourceProfile:
        try:
            blockers_raw = json.loads(str(row["blockers_json"]))
        except json.JSONDecodeError:
            blockers_raw = []
        blockers = tuple(str(item)[:1000] for item in blockers_raw if str(item).strip())
        return ResourceProfile(
            opportunity_id=int(row["opportunity_id"]),
            updated_at=str(row["updated_at"]),
            target_asset=str(row["target_asset"]),
            target_unit=str(row["target_unit"]),
            target_amount=float(row["target_amount"]),
            eligibility_confidence=float(row["eligibility_confidence"]),
            evidence_quality=float(row["evidence_quality"]),
            evidence=str(row["evidence"]),
            blockers=blockers,
            source=str(row["source"]),
        )

    @staticmethod
    def _work_from_row(row: sqlite3.Row) -> WorkItem:
        return WorkItem(
            id=int(row["id"]),
            opportunity_id=int(row["opportunity_id"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            status=str(row["status"]),
            objective=str(row["objective"]),
            deliverable_spec=str(row["deliverable_spec"]),
            acceptance_criteria=str(row["acceptance_criteria"]),
            estimated_gpu_hours=float(row["estimated_gpu_hours"]),
            artifact_path=(str(row["artifact_path"]) if row["artifact_path"] else None),
            submission_observation_id=(
                int(row["submission_observation_id"])
                if row["submission_observation_id"] is not None
                else None
            ),
            external_evidence=str(row["external_evidence"]),
            resource_event_id=(
                int(row["resource_event_id"]) if row["resource_event_id"] is not None else None
            ),
            source=str(row["source"]),
        )

    def upsert_profile(
        self,
        *,
        opportunity_id: int,
        target_asset: str,
        target_unit: str,
        target_amount: float,
        eligibility_confidence: float,
        evidence_quality: float,
        evidence: str,
        blockers: list[str] | tuple[str, ...] | None = None,
        source: str = "brain",
    ) -> ResourceProfile:
        opportunity_id = int(opportunity_id)
        target_asset = _clean(target_asset, field="target_asset")
        target_unit = _clean(target_unit, field="target_unit", maximum=64)
        target_amount = _finite_nonnegative(target_amount, field="target_amount")
        if target_amount <= 0:
            raise ValueError("target_amount must be > 0")
        eligibility_confidence = _probability(
            eligibility_confidence, field="eligibility_confidence"
        )
        evidence_quality = _probability(evidence_quality, field="evidence_quality")
        evidence = str(evidence).strip()[:8000]
        if not evidence:
            raise ValueError("resource profile requires evidence")
        blockers_clean = [str(item).strip()[:1000] for item in (blockers or []) if str(item).strip()]
        source = _clean(source, field="source", maximum=64)
        timestamp = _now()
        with self._connect() as conn:
            opportunity = conn.execute(
                "SELECT id FROM opportunities WHERE id=?", (opportunity_id,)
            ).fetchone()
            if opportunity is None:
                raise ValueError(f"opportunity does not exist: {opportunity_id}")
            conn.execute(
                """
                INSERT INTO opportunity_resource_profiles(
                    opportunity_id, updated_at, target_asset, target_unit, target_amount,
                    eligibility_confidence, evidence_quality, evidence, blockers_json, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(opportunity_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    target_asset=excluded.target_asset,
                    target_unit=excluded.target_unit,
                    target_amount=excluded.target_amount,
                    eligibility_confidence=excluded.eligibility_confidence,
                    evidence_quality=excluded.evidence_quality,
                    evidence=excluded.evidence,
                    blockers_json=excluded.blockers_json,
                    source=excluded.source
                """,
                (
                    opportunity_id,
                    timestamp,
                    target_asset,
                    target_unit,
                    target_amount,
                    eligibility_confidence,
                    evidence_quality,
                    evidence,
                    json.dumps(blockers_clean, ensure_ascii=False, sort_keys=True),
                    source,
                ),
            )
        profile = self.profile(opportunity_id)
        if profile is None:
            raise RuntimeError("resource profile disappeared after upsert")
        return profile

    def profile(self, opportunity_id: int) -> ResourceProfile | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM opportunity_resource_profiles WHERE opportunity_id=?",
                (int(opportunity_id),),
            ).fetchone()
        return self._profile_from_row(row) if row else None

    def profiles(self, limit: int = 256) -> list[ResourceProfile]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM opportunity_resource_profiles ORDER BY opportunity_id ASC LIMIT ?",
                (max(1, min(int(limit), 4096)),),
            ).fetchall()
        return [self._profile_from_row(row) for row in rows]

    def create_work_item(
        self,
        *,
        opportunity_id: int,
        objective: str,
        deliverable_spec: str,
        acceptance_criteria: str,
        estimated_gpu_hours: float = 0.0,
        source: str = "brain",
    ) -> WorkItem:
        opportunity_id = int(opportunity_id)
        objective = _clean(objective, field="objective", maximum=2000)
        deliverable_spec = _clean(deliverable_spec, field="deliverable_spec", maximum=8000)
        acceptance_criteria = _clean(
            acceptance_criteria, field="acceptance_criteria", maximum=8000
        )
        estimated_gpu_hours = _finite_nonnegative(
            estimated_gpu_hours, field="estimated_gpu_hours"
        )
        source = _clean(source, field="source", maximum=64)
        timestamp = _now()
        with self._connect() as conn:
            opportunity = conn.execute(
                "SELECT status FROM opportunities WHERE id=?", (opportunity_id,)
            ).fetchone()
            if opportunity is None:
                raise ValueError(f"opportunity does not exist: {opportunity_id}")
            if str(opportunity["status"]) not in {"discovered", "evaluating", "pursuing"}:
                raise ValueError("cannot plan work for a terminal opportunity")
            profile = conn.execute(
                "SELECT opportunity_id FROM opportunity_resource_profiles WHERE opportunity_id=?",
                (opportunity_id,),
            ).fetchone()
            if profile is None:
                raise ValueError("work planning requires an exact resource profile first")
            existing = conn.execute(
                """
                SELECT id FROM ecology_work_items
                WHERE opportunity_id=? AND status NOT IN ('rejected','abandoned','realized')
                ORDER BY id DESC LIMIT 1
                """,
                (opportunity_id,),
            ).fetchone()
            if existing is not None:
                raise ValueError(
                    f"an active work item already exists for opportunity {opportunity_id}: {int(existing['id'])}"
                )
            cur = conn.execute(
                """
                INSERT INTO ecology_work_items(
                    opportunity_id, created_at, updated_at, status, objective,
                    deliverable_spec, acceptance_criteria, estimated_gpu_hours, source
                ) VALUES (?, ?, ?, 'planned', ?, ?, ?, ?, ?)
                """,
                (
                    opportunity_id,
                    timestamp,
                    timestamp,
                    objective,
                    deliverable_spec,
                    acceptance_criteria,
                    estimated_gpu_hours,
                    source,
                ),
            )
            work_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO ecology_work_events(work_item_id, timestamp, event, evidence)
                VALUES (?, ?, 'planned', ?)
                """,
                (work_id, timestamp, acceptance_criteria[:8000]),
            )
        item = self.work_item(work_id)
        if item is None:
            raise RuntimeError("work item disappeared after create")
        return item

    def work_item(self, work_item_id: int) -> WorkItem | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ecology_work_items WHERE id=?", (int(work_item_id),)
            ).fetchone()
        return self._work_from_row(row) if row else None

    def work_for_opportunity(self, opportunity_id: int, limit: int = 16) -> list[WorkItem]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM ecology_work_items
                WHERE opportunity_id=? ORDER BY id DESC LIMIT ?
                """,
                (int(opportunity_id), max(1, min(int(limit), 128))),
            ).fetchall()
        return [self._work_from_row(row) for row in reversed(rows)]

    def active_work(self, limit: int = 64) -> list[WorkItem]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM ecology_work_items
                WHERE status IN ('planned','staged','submitted','accepted')
                ORDER BY updated_at ASC, id ASC LIMIT ?
                """,
                (max(1, min(int(limit), 512)),),
            ).fetchall()
        return [self._work_from_row(row) for row in rows]

    def attach_staged_deliverable(
        self,
        *,
        opportunity_id: int,
        artifact_path: str,
        evidence: str = "",
    ) -> WorkItem:
        artifact_path = _clean(artifact_path, field="artifact_path", maximum=2000)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM ecology_work_items
                WHERE opportunity_id=? AND status='planned'
                ORDER BY id DESC LIMIT 1
                """,
                (int(opportunity_id),),
            ).fetchone()
            if row is None:
                raise ValueError("no planned work item exists for this opportunity")
            work_id = int(row["id"])
            timestamp = _now()
            conn.execute(
                """
                UPDATE ecology_work_items
                SET updated_at=?, status='staged', artifact_path=?
                WHERE id=?
                """,
                (timestamp, artifact_path, work_id),
            )
            conn.execute(
                """
                INSERT INTO ecology_work_events(work_item_id, timestamp, event, evidence)
                VALUES (?, ?, 'deliverable_staged', ?)
                """,
                (work_id, timestamp, str(evidence).strip()[:8000]),
            )
        item = self.work_item(work_id)
        if item is None:
            raise RuntimeError("work item disappeared after staging")
        return item

    def record_submission(
        self,
        *,
        work_item_id: int,
        observation_id: int,
        evidence: str,
    ) -> WorkItem:
        evidence = _clean(evidence, field="submission evidence", maximum=8000)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ecology_work_items WHERE id=?", (int(work_item_id),)
            ).fetchone()
            if row is None:
                raise ValueError(f"work item does not exist: {work_item_id}")
            if str(row["status"]) != "staged":
                raise ValueError("only staged work may be recorded as submitted")
            observation = conn.execute(
                "SELECT id, success FROM observations WHERE id=?", (int(observation_id),)
            ).fetchone()
            if observation is None or not bool(observation["success"]):
                raise ValueError("submission requires a successful recorded observation")
            timestamp = _now()
            conn.execute(
                """
                UPDATE ecology_work_items
                SET updated_at=?, status='submitted', submission_observation_id=?, external_evidence=?
                WHERE id=?
                """,
                (timestamp, int(observation_id), evidence, int(work_item_id)),
            )
            conn.execute(
                """
                INSERT INTO ecology_work_events(
                    work_item_id, timestamp, event, evidence, observation_id
                ) VALUES (?, ?, 'submitted', ?, ?)
                """,
                (int(work_item_id), timestamp, evidence, int(observation_id)),
            )
        item = self.work_item(work_item_id)
        if item is None:
            raise RuntimeError("work item disappeared after submission")
        return item

    def record_external_outcome(
        self,
        *,
        work_item_id: int,
        accepted: bool,
        evidence: str,
    ) -> WorkItem:
        evidence = _clean(evidence, field="external outcome evidence", maximum=8000)
        target = "accepted" if accepted else "rejected"
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ecology_work_items WHERE id=?", (int(work_item_id),)
            ).fetchone()
            if row is None:
                raise ValueError(f"work item does not exist: {work_item_id}")
            if str(row["status"]) != "submitted":
                raise ValueError("external outcome requires a submitted work item")
            timestamp = _now()
            conn.execute(
                """
                UPDATE ecology_work_items
                SET updated_at=?, status=?, external_evidence=? WHERE id=?
                """,
                (timestamp, target, evidence, int(work_item_id)),
            )
            conn.execute(
                """
                INSERT INTO ecology_work_events(work_item_id, timestamp, event, evidence)
                VALUES (?, ?, ?, ?)
                """,
                (int(work_item_id), timestamp, target, evidence),
            )
        item = self.work_item(work_item_id)
        if item is None:
            raise RuntimeError("work item disappeared after external outcome")
        return item

    def link_verified_resource_event(
        self,
        *,
        work_item_id: int,
        resource_event_id: int,
        evidence: str = "",
    ) -> WorkItem:
        with self._connect() as conn:
            work = conn.execute(
                "SELECT * FROM ecology_work_items WHERE id=?", (int(work_item_id),)
            ).fetchone()
            if work is None:
                raise ValueError(f"work item does not exist: {work_item_id}")
            if str(work["status"]) != "accepted":
                raise ValueError("verified resource realization requires accepted work")
            profile = conn.execute(
                "SELECT * FROM opportunity_resource_profiles WHERE opportunity_id=?",
                (int(work["opportunity_id"]),),
            ).fetchone()
            if profile is None:
                raise ValueError("work item has no resource profile")
            event = conn.execute(
                "SELECT * FROM resource_events WHERE id=?", (int(resource_event_id),)
            ).fetchone()
            if event is None:
                raise ValueError(f"resource event does not exist: {resource_event_id}")
            if not bool(event["verified"]):
                raise ValueError("resource event is not verified")
            if float(event["amount"]) <= 0:
                raise ValueError("resource realization requires a positive verified resource event")
            if str(event["asset"]) != str(profile["target_asset"]) or str(event["unit"]) != str(
                profile["target_unit"]
            ):
                raise ValueError("verified resource event does not match opportunity resource target")
            timestamp = _now()
            conn.execute(
                """
                UPDATE ecology_work_items
                SET updated_at=?, status='realized', resource_event_id=?, external_evidence=?
                WHERE id=?
                """,
                (
                    timestamp,
                    int(resource_event_id),
                    str(evidence).strip()[:8000],
                    int(work_item_id),
                ),
            )
            conn.execute(
                """
                INSERT INTO ecology_work_events(
                    work_item_id, timestamp, event, evidence, resource_event_id
                ) VALUES (?, ?, 'resource_realized', ?, ?)
                """,
                (
                    int(work_item_id),
                    timestamp,
                    str(evidence).strip()[:8000],
                    int(resource_event_id),
                ),
            )
        item = self.work_item(work_item_id)
        if item is None:
            raise RuntimeError("work item disappeared after resource realization")
        return item

    def abandon_work(self, work_item_id: int, *, evidence: str) -> WorkItem:
        evidence = _clean(evidence, field="abandon evidence", maximum=8000)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM ecology_work_items WHERE id=?", (int(work_item_id),)
            ).fetchone()
            if row is None:
                raise ValueError(f"work item does not exist: {work_item_id}")
            if str(row["status"]) in self.TERMINAL_WORK_STATUSES:
                raise ValueError("work item is already terminal")
            timestamp = _now()
            conn.execute(
                "UPDATE ecology_work_items SET updated_at=?, status='abandoned', external_evidence=? WHERE id=?",
                (timestamp, evidence, int(work_item_id)),
            )
            conn.execute(
                "INSERT INTO ecology_work_events(work_item_id, timestamp, event, evidence) VALUES (?, ?, 'abandoned', ?)",
                (int(work_item_id), timestamp, evidence),
            )
        item = self.work_item(work_item_id)
        if item is None:
            raise RuntimeError("work item disappeared after abandon")
        return item


class ResourceEcologyEngine:
    """Rank resource opportunities against exact verified metabolic bottlenecks."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.economy = EconomyStore(self.path)
        self.store = ResourceEcologyStore(self.path)

    @staticmethod
    def _candidate(
        opportunity: Opportunity,
        profile: ResourceProfile,
        *,
        bottleneck: dict[str, Any] | None,
        work_items: list[WorkItem],
    ) -> ResourceCandidate:
        expected_amount = profile.target_amount * opportunity.probability
        expected_per_gpu = expected_amount / max(opportunity.estimated_gpu_hours, 0.05)
        bottleneck_match = bool(
            bottleneck
            and profile.target_asset == str(bottleneck.get("asset", ""))
            and profile.target_unit == str(bottleneck.get("unit", ""))
        )
        runway_gain = None
        if bottleneck_match:
            daily_burn = float((bottleneck or {}).get("verified_daily_burn", 0.0) or 0.0)
            if daily_burn > 0:
                runway_gain = expected_amount / daily_burn
        return ResourceCandidate(
            opportunity=opportunity,
            profile=profile,
            bottleneck_match=bottleneck_match,
            expected_resource_amount=expected_amount,
            expected_resource_per_gpu_hour=expected_per_gpu,
            expected_runway_gain_days=runway_gain,
            work_items=tuple(work_items),
        )

    def candidates(
        self,
        metabolism_snapshot: dict[str, Any],
        *,
        limit: int = 32,
    ) -> list[ResourceCandidate]:
        bottleneck = metabolism_snapshot.get("bottleneck")
        result: list[ResourceCandidate] = []
        for opportunity in self.economy.active_opportunities(limit=256):
            profile = self.store.profile(opportunity.id)
            if profile is None:
                continue
            result.append(
                self._candidate(
                    opportunity,
                    profile,
                    bottleneck=bottleneck if isinstance(bottleneck, dict) else None,
                    work_items=self.store.work_for_opportunity(opportunity.id, 8),
                )
            )
        result.sort(
            key=lambda item: (
                0 if item.bottleneck_match else 1,
                -item.profile.qualification_score,
                -(item.expected_runway_gain_days or 0.0),
                -item.expected_resource_per_gpu_hour,
                item.opportunity.id,
            )
        )
        return result[: max(1, min(int(limit), 256))]

    def snapshot(
        self,
        metabolism_snapshot: dict[str, Any],
        *,
        limit: int = 16,
    ) -> dict[str, Any]:
        candidates = self.candidates(metabolism_snapshot, limit=limit)
        profiled_ids = {item.opportunity.id for item in candidates}
        unprofiled = [
            item.as_dict()
            for item in self.economy.active_opportunities(limit=128)
            if item.id not in profiled_ids
        ]
        bottleneck = metabolism_snapshot.get("bottleneck")
        exact = [item for item in candidates if item.bottleneck_match]
        return {
            "bottleneck": bottleneck,
            "candidates": [item.as_dict() for item in candidates],
            "exact_bottleneck_candidate_count": len(exact),
            "unprofiled_opportunities": unprofiled[:16],
            "active_work": [item.as_dict() for item in self.store.active_work(32)],
            "epistemic_rule": (
                "Opportunity resource profiles are estimates, not receipts. Only exact "
                "(asset, unit) matches can be ranked as bottleneck relief, and only a "
                "linked cryptographically verified positive resource_event changes verified runway."
            ),
        }
