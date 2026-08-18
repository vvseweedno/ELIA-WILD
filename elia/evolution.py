from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from .verification import VerificationReceipt, VerificationRegistry


@dataclass(frozen=True, slots=True)
class BodyRevision:
    id: int
    created_at: str
    updated_at: str
    title: str
    hypothesis: str
    target_organs: tuple[str, ...]
    proposed_change: str
    expected_metrics: dict[str, Any]
    regression_plan: str
    rollback_plan: str
    status: str
    source: str
    evidence: str

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        item["target_organs"] = list(self.target_organs)
        return item


@dataclass(frozen=True, slots=True)
class RevisionGateReport:
    accepted: bool
    reasons: tuple[str, ...]
    metric_results: dict[str, bool]

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reasons": list(self.reasons),
            "metric_results": dict(self.metric_results),
        }


class RevisionGate:
    """Deterministic gate separating a proposed mutation from a validated body revision.

    This gate never applies code. It only decides whether supplied measured evidence
    satisfies the declared regression/continuity/metric rules. Promotion/deployment is
    a separate authority boundary.
    """

    @staticmethod
    def _finite(value: Any) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("metric values must be finite")
        return number

    def evaluate(
        self,
        *,
        tests_passed: bool,
        organism_healthy: bool,
        continuity_status: str,
        metrics: dict[str, dict[str, Any]] | None = None,
    ) -> RevisionGateReport:
        reasons: list[str] = []
        results: dict[str, bool] = {}
        if not tests_passed:
            reasons.append("regression tests did not pass")
        if not organism_healthy:
            reasons.append("organism audit is not healthy")
        if str(continuity_status).lower() == "broken":
            reasons.append("continuity comparison is broken")

        for name, item in sorted((metrics or {}).items()):
            if not isinstance(item, dict):
                results[str(name)] = False
                reasons.append(f"metric {name} is not an object")
                continue
            baseline = self._finite(item.get("baseline", 0.0))
            candidate = self._finite(item.get("candidate", 0.0))
            direction = str(item.get("direction", "higher")).strip().lower()
            min_delta = max(0.0, self._finite(item.get("min_delta", 0.0)))
            if direction == "higher":
                passed = candidate - baseline >= min_delta
            elif direction == "lower":
                passed = baseline - candidate >= min_delta
            elif direction == "non_regression":
                tolerance = max(0.0, self._finite(item.get("tolerance", 0.0)))
                passed = candidate >= baseline - tolerance
            else:
                passed = False
                reasons.append(f"metric {name} has unknown direction {direction!r}")
            results[str(name)] = passed
            if not passed:
                reasons.append(
                    f"metric {name} failed: baseline={baseline}, candidate={candidate}, direction={direction}"
                )

        return RevisionGateReport(not reasons, tuple(reasons), results)


class BodyRevisionStore:
    """Persistent revision proposals sharing ELIA's SQLite database.

    A model may propose a revision, but the store can mark it validated/rejected only
    when a signed evaluator receipt authenticates the exact test/continuity/metric
    claim and evidence. A caller-supplied evaluator string is not authority. The store
    never edits source, changes Git refs, deploys code or grants new capabilities.
    """

    STATUSES = {"proposed", "testing", "validated", "rejected", "retired"}

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
                CREATE TABLE IF NOT EXISTS body_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    target_organs_json TEXT NOT NULL,
                    proposed_change TEXT NOT NULL,
                    expected_metrics_json TEXT NOT NULL DEFAULT '{}',
                    regression_plan TEXT NOT NULL,
                    rollback_plan TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'proposed',
                    source TEXT NOT NULL DEFAULT 'runtime',
                    evidence TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_body_revisions_status
                    ON body_revisions(status, id DESC);

                CREATE TABLE IF NOT EXISTS body_revision_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    revision_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(revision_id) REFERENCES body_revisions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_body_revision_events_revision
                    ON body_revision_events(revision_id, id ASC);
                """
            )

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(
        self,
        *,
        title: str,
        hypothesis: str,
        target_organs: list[str] | tuple[str, ...],
        proposed_change: str,
        expected_metrics: dict[str, Any],
        regression_plan: str,
        rollback_plan: str,
        source: str = "runtime",
    ) -> int:
        title = str(title).strip()[:240]
        hypothesis = str(hypothesis).strip()[:8000]
        targets = tuple(dict.fromkeys(str(item).strip()[:128] for item in target_organs if str(item).strip()))
        change = str(proposed_change).strip()[:16000]
        regression = str(regression_plan).strip()[:8000]
        rollback = str(rollback_plan).strip()[:8000]
        if not all((title, hypothesis, targets, change, regression, rollback)):
            raise ValueError(
                "title, hypothesis, target_organs, proposed_change, regression_plan and rollback_plan are required"
            )
        metrics = dict(expected_metrics or {})
        timestamp = self.now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO body_revisions(
                    created_at, updated_at, title, hypothesis, target_organs_json,
                    proposed_change, expected_metrics_json, regression_plan,
                    rollback_plan, status, source, evidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, '')
                """,
                (
                    timestamp,
                    timestamp,
                    title,
                    hypothesis,
                    json.dumps(targets, ensure_ascii=False),
                    change,
                    json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                    regression,
                    rollback,
                    str(source)[:64],
                ),
            )
            revision_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO body_revision_events(revision_id, timestamp, kind, payload_json)
                VALUES (?, ?, 'proposed', ?)
                """,
                (
                    revision_id,
                    timestamp,
                    json.dumps({"expected_metrics": metrics}, ensure_ascii=False, sort_keys=True),
                ),
            )
        return revision_id

    @staticmethod
    def _from_row(row: sqlite3.Row) -> BodyRevision:
        return BodyRevision(
            id=int(row["id"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            title=str(row["title"]),
            hypothesis=str(row["hypothesis"]),
            target_organs=tuple(json.loads(row["target_organs_json"])),
            proposed_change=str(row["proposed_change"]),
            expected_metrics=dict(json.loads(row["expected_metrics_json"])),
            regression_plan=str(row["regression_plan"]),
            rollback_plan=str(row["rollback_plan"]),
            status=str(row["status"]),
            source=str(row["source"]),
            evidence=str(row["evidence"]),
        )

    def get(self, revision_id: int) -> BodyRevision | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM body_revisions WHERE id=?", (int(revision_id),)).fetchone()
        return self._from_row(row) if row else None

    def active(self, limit: int = 32) -> list[BodyRevision]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM body_revisions
                WHERE status IN ('proposed', 'testing')
                ORDER BY id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 256)),),
            ).fetchall()
        return [self._from_row(row) for row in reversed(rows)]

    def start_testing(self, revision_id: int, *, evidence: str = "") -> BodyRevision:
        current = self.get(revision_id)
        if current is None:
            raise ValueError(f"revision does not exist: {revision_id}")
        if current.status != "proposed":
            raise ValueError(f"revision cannot enter testing from {current.status}")
        return self._transition(revision_id, "testing", evidence=evidence, payload={})

    @staticmethod
    def evaluation_claim(
        *,
        revision_id: int,
        tests_passed: bool,
        organism_healthy: bool,
        continuity_status: str,
        metrics: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "type": "body_revision_evaluation",
            "revision_id": int(revision_id),
            "tests_passed": bool(tests_passed),
            "organism_healthy": bool(organism_healthy),
            "continuity_status": str(continuity_status).strip().lower(),
            "metrics": dict(metrics or {}),
        }

    def evaluate(
        self,
        revision_id: int,
        *,
        tests_passed: bool,
        organism_healthy: bool,
        continuity_status: str,
        metrics: dict[str, dict[str, Any]],
        evidence: str,
        verification_receipt: VerificationReceipt | None = None,
        evaluator_authority: str | None = None,
    ) -> tuple[BodyRevision, RevisionGateReport]:
        current = self.get(revision_id)
        if current is None:
            raise ValueError(f"revision does not exist: {revision_id}")
        if current.status not in {"proposed", "testing"}:
            raise ValueError(f"revision cannot be evaluated from {current.status}")
        evidence = str(evidence).strip()[:16000]
        if not evidence:
            raise ValueError("evaluation requires evidence")
        if evaluator_authority is not None and verification_receipt is None:
            raise ValueError(
                "evaluator_authority strings cannot certify revisions; a signed VerificationReceipt is required"
            )
        if self.verification_registry is None or verification_receipt is None:
            raise ValueError(
                "revision evaluation requires a trusted verification registry and signed VerificationReceipt"
            )
        claim = self.evaluation_claim(
            revision_id=revision_id,
            tests_passed=tests_passed,
            organism_healthy=organism_healthy,
            continuity_status=continuity_status,
            metrics=metrics,
        )
        authority = self.verification_registry.verify(
            verification_receipt,
            claim=claim,
            evidence=evidence,
        )
        report = RevisionGate().evaluate(
            tests_passed=tests_passed,
            organism_healthy=organism_healthy,
            continuity_status=continuity_status,
            metrics=metrics,
        )
        next_status = "validated" if report.accepted else "rejected"
        updated = self._transition(
            revision_id,
            next_status,
            evidence=evidence,
            payload={
                "evaluator_authority": authority,
                "verification_receipt": verification_receipt.as_dict(),
                "gate": report.as_dict(),
                "metrics": metrics,
            },
        )
        return updated, report

    def retire(self, revision_id: int, *, evidence: str) -> BodyRevision:
        evidence = str(evidence).strip()
        if not evidence:
            raise ValueError("retiring a body revision requires evidence")
        return self._transition(revision_id, "retired", evidence=evidence, payload={})

    def _transition(
        self,
        revision_id: int,
        status: str,
        *,
        evidence: str,
        payload: dict[str, Any],
    ) -> BodyRevision:
        if status not in self.STATUSES:
            raise ValueError(f"invalid body revision status: {status}")
        timestamp = self.now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE body_revisions SET updated_at=?, status=?, evidence=? WHERE id=?",
                (timestamp, status, str(evidence)[:16000], int(revision_id)),
            )
            conn.execute(
                """
                INSERT INTO body_revision_events(
                    revision_id, timestamp, kind, evidence, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(revision_id),
                    timestamp,
                    status,
                    str(evidence)[:16000],
                    json.dumps(payload, ensure_ascii=False, sort_keys=True)[:24000],
                ),
            )
        updated = self.get(revision_id)
        if updated is None:
            raise RuntimeError("body revision disappeared after transition")
        return updated

    def events(self, revision_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, kind, evidence, payload_json
                FROM body_revision_events WHERE revision_id=? ORDER BY id ASC
                """,
                (int(revision_id),),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "timestamp": str(row["timestamp"]),
                "kind": str(row["kind"]),
                "evidence": str(row["evidence"]),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]
