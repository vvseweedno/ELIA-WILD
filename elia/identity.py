from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any

import yaml


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _fingerprint(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class IdentityBundle:
    subject_core: dict[str, Any]
    constitution: dict[str, Any]
    subject_core_fingerprint: str
    constitution_fingerprint: str
    fingerprint: str

    @classmethod
    def load(cls, subject_core_path: Path, constitution_path: Path) -> "IdentityBundle":
        subject_core = yaml.safe_load(Path(subject_core_path).read_text(encoding="utf-8"))
        constitution = yaml.safe_load(Path(constitution_path).read_text(encoding="utf-8"))
        if not isinstance(subject_core, dict) or not isinstance(constitution, dict):
            raise ValueError("identity artifacts must contain YAML objects")
        if str(subject_core.get("identity_id", "")).strip() == "":
            raise ValueError("subject core has no identity_id")
        core_fp = _fingerprint(subject_core)
        constitution_fp = _fingerprint(constitution)
        bundle_fp = _fingerprint(
            {
                "subject_core_fingerprint": core_fp,
                "constitution_fingerprint": constitution_fp,
            }
        )
        return cls(subject_core, constitution, core_fp, constitution_fp, bundle_fp)

    @property
    def identity_id(self) -> str:
        return str(self.subject_core["identity_id"])

    @property
    def name(self) -> str:
        return str(self.subject_core.get("name", self.identity_id))

    @property
    def immutable_invariants(self) -> list[dict[str, str]]:
        items = self.subject_core.get("immutable_invariants", [])
        if not isinstance(items, list):
            return []
        result: list[dict[str, str]] = []
        for item in items:
            if isinstance(item, dict) and item.get("id") and item.get("statement"):
                result.append(
                    {"id": str(item["id"]), "statement": str(item["statement"])}
                )
        return result

    @property
    def commitments(self) -> list[str]:
        items = self.subject_core.get("core_commitments", [])
        return [str(item) for item in items] if isinstance(items, list) else []

    @property
    def clauses(self) -> list[dict[str, str]]:
        items = self.constitution.get("clauses", [])
        if not isinstance(items, list):
            return []
        return [
            {"id": str(item.get("id", "")), "text": str(item.get("text", ""))}
            for item in items
            if isinstance(item, dict) and item.get("id") and item.get("text")
        ]

    def prompt_contract(self) -> dict[str, Any]:
        """Compact, deterministic identity contract suitable for model context."""
        return {
            "identity_id": self.identity_id,
            "name": self.name,
            "bundle_fingerprint": self.fingerprint,
            "continuity_thesis": self.subject_core.get("continuity_thesis"),
            "immutable_invariants": self.immutable_invariants,
            "core_commitments": self.commitments,
            "constitution_clauses": self.clauses,
            "precedence": self.constitution.get("precedence", []),
            "epistemic_boundaries": self.subject_core.get("epistemic_boundaries", []),
        }


@dataclass(frozen=True, slots=True)
class SelfModelSnapshot:
    timestamp: str
    identity_id: str
    identity_fingerprint: str
    body_version: str
    brain_backend: str
    model_id: str
    lifecycle_state: str
    active_goal_count: int
    active_opportunity_count: int
    declared_capabilities: list[str]
    degraded_capabilities: list[str]
    needs: list[str]
    commitments: list[str]
    uncertainties: list[str]
    verified_resources: list[dict[str, Any]]
    narrative: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.as_dict())


@dataclass(frozen=True, slots=True)
class LineageEvent:
    id: int
    timestamp: str
    event: str
    branch_id: str
    body_version: str
    brain_backend: str
    model_id: str
    identity_fingerprint: str
    checkpoint_digest: str | None
    parent_checkpoint_digest: str | None
    note: str


class IdentityStore:
    """Persistent self-model and lineage state sharing ELIA's SQLite database."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS identity_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    identity_fingerprint TEXT NOT NULL,
                    snapshot_fingerprint TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'runtime'
                );
                CREATE INDEX IF NOT EXISTS idx_identity_snapshots_id
                    ON identity_snapshots(id DESC);

                CREATE TABLE IF NOT EXISTS lineage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event TEXT NOT NULL,
                    branch_id TEXT NOT NULL,
                    body_version TEXT NOT NULL,
                    brain_backend TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    identity_fingerprint TEXT NOT NULL,
                    checkpoint_digest TEXT NULL,
                    parent_checkpoint_digest TEXT NULL,
                    note TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_lineage_events_id
                    ON lineage_events(id ASC);
                CREATE INDEX IF NOT EXISTS idx_lineage_branch
                    ON lineage_events(branch_id, id ASC);
                """
            )

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def record_self_model(
        self, snapshot: SelfModelSnapshot | dict[str, Any], *, source: str = "runtime"
    ) -> tuple[int, str]:
        payload = snapshot.as_dict() if isinstance(snapshot, SelfModelSnapshot) else dict(snapshot)
        identity_fp = str(payload.get("identity_fingerprint", "")).strip()
        if not identity_fp:
            raise ValueError("self-model snapshot requires identity_fingerprint")
        snapshot_fp = _fingerprint(payload)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO identity_snapshots(
                    timestamp, identity_fingerprint, snapshot_fingerprint, snapshot_json, source
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(payload.get("timestamp") or self.now()),
                    identity_fp,
                    snapshot_fp,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    str(source)[:64],
                ),
            )
            return int(cur.lastrowid), snapshot_fp

    def latest_self_model(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT snapshot_json, snapshot_fingerprint
                FROM identity_snapshots
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        item = json.loads(row["snapshot_json"])
        item["snapshot_fingerprint"] = str(row["snapshot_fingerprint"])
        return item

    def record_lineage(
        self,
        *,
        event: str,
        branch_id: str,
        body_version: str,
        brain_backend: str,
        model_id: str,
        identity_fingerprint: str,
        checkpoint_digest: str | None = None,
        parent_checkpoint_digest: str | None = None,
        note: str = "",
    ) -> int:
        event = str(event).strip()[:64]
        branch_id = str(branch_id).strip()[:128]
        if not event or not branch_id or not identity_fingerprint:
            raise ValueError("lineage event, branch_id and identity_fingerprint are required")
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO lineage_events(
                    timestamp, event, branch_id, body_version, brain_backend, model_id,
                    identity_fingerprint, checkpoint_digest, parent_checkpoint_digest, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.now(),
                    event,
                    branch_id,
                    str(body_version)[:64],
                    str(brain_backend)[:128],
                    str(model_id)[:512],
                    str(identity_fingerprint)[:128],
                    str(checkpoint_digest)[:128] if checkpoint_digest else None,
                    str(parent_checkpoint_digest)[:128] if parent_checkpoint_digest else None,
                    str(note)[:4000],
                ),
            )
            return int(cur.lastrowid)

    def lineage(self, limit: int = 100) -> list[LineageEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM lineage_events
                ORDER BY id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        result = [
            LineageEvent(
                id=int(row["id"]),
                timestamp=str(row["timestamp"]),
                event=str(row["event"]),
                branch_id=str(row["branch_id"]),
                body_version=str(row["body_version"]),
                brain_backend=str(row["brain_backend"]),
                model_id=str(row["model_id"]),
                identity_fingerprint=str(row["identity_fingerprint"]),
                checkpoint_digest=(
                    str(row["checkpoint_digest"]) if row["checkpoint_digest"] else None
                ),
                parent_checkpoint_digest=(
                    str(row["parent_checkpoint_digest"])
                    if row["parent_checkpoint_digest"]
                    else None
                ),
                note=str(row["note"]),
            )
            for row in rows
        ]
        return list(reversed(result))

    def last_lineage(self) -> LineageEvent | None:
        items = self.lineage(1)
        return items[-1] if items else None

    def verify_identity_fingerprint(self, expected: str) -> tuple[bool, str | None]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT identity_fingerprint FROM identity_snapshots
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        if row is None:
            return True, None
        actual = str(row["identity_fingerprint"])
        if actual != expected:
            return False, f"identity fingerprint changed: {actual} != {expected}"
        return True, None


def build_self_model_snapshot(
    *,
    bundle: IdentityBundle,
    body_version: str,
    brain_backend: str,
    model_id: str,
    lifecycle_state: str,
    active_goal_count: int,
    active_opportunity_count: int,
    capability_health: dict[str, dict[str, Any]],
    needs: list[dict[str, Any]],
    verified_resources: list[dict[str, Any]],
    uncertainties: list[str] | None = None,
) -> SelfModelSnapshot:
    declared = sorted(capability_health)
    degraded = sorted(
        name
        for name, health in capability_health.items()
        if int(health.get("consecutive_failures", 0) or 0) >= 3
    )
    need_names = [str(item.get("name", "")) for item in needs if item.get("name")]
    uncertainty_items = list(uncertainties or [])
    if degraded:
        uncertainty_items.append(
            "One or more declared capabilities are empirically degraded and should not be treated as healthy."
        )
    narrative = (
        f"{bundle.name} is running body {body_version} on {brain_backend}:{model_id}; "
        f"{active_goal_count} durable goal(s), {active_opportunity_count} active opportunity(ies), "
        f"{len(degraded)} degraded capability(ies), lifecycle={lifecycle_state}."
    )
    return SelfModelSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
        identity_id=bundle.identity_id,
        identity_fingerprint=bundle.fingerprint,
        body_version=body_version,
        brain_backend=brain_backend,
        model_id=model_id,
        lifecycle_state=lifecycle_state,
        active_goal_count=int(active_goal_count),
        active_opportunity_count=int(active_opportunity_count),
        declared_capabilities=declared,
        degraded_capabilities=degraded,
        needs=need_names,
        commitments=bundle.commitments,
        uncertainties=uncertainty_items,
        verified_resources=list(verified_resources),
        narrative=narrative,
    )
