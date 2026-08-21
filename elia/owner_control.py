from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

import yaml


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_text() -> str:
    return _now().isoformat()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def arguments_fingerprint(action_name: str, arguments: dict[str, Any]) -> str:
    return sha256(_canonical([str(action_name), arguments]).encode("utf-8")).hexdigest()


class OwnerControlError(PermissionError):
    pass


class OwnerKillSwitch(OwnerControlError):
    pass


class DelegationRevoked(OwnerControlError):
    pass


class DelegationLeaseExpired(OwnerControlError):
    pass


class HumanApprovalRequired(OwnerControlError):
    pass


@dataclass(frozen=True, slots=True)
class OwnerMandate:
    schema_version: int
    precedence: tuple[str, ...]
    require_external_lease: bool
    approval_required_actions: tuple[str, ...]
    default_lease_hours: float
    fingerprint: str

    @classmethod
    def load(cls, path: Path, *, required: bool = False) -> "OwnerMandate":
        path = Path(path)
        if path.is_file():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        elif required:
            raise FileNotFoundError(f"owner mandate is missing: {path}")
        else:
            raw = {}
        precedence = tuple(
            str(item)
            for item in raw.get(
                "precedence",
                [
                    "host_platform_policy_and_access_controls",
                    "explicit_owner_mandate_and_revocation",
                    "delegated_mission",
                    "continuity",
                    "resource_acquisition",
                    "curiosity",
                ],
            )
        )
        lease = dict(raw.get("delegation_lease") or {})
        approvals = dict(raw.get("human_approval") or {})
        canonical = _canonical(raw if raw else {
            "schema_version": 1,
            "precedence": list(precedence),
            "delegation_lease": {"require_for_external_effects": False, "default_hours": 24},
            "human_approval": {"required_actions": ["submit_work"]},
        })
        return cls(
            schema_version=int(raw.get("schema_version", 1)),
            precedence=precedence,
            require_external_lease=bool(lease.get("require_for_external_effects", False)),
            approval_required_actions=tuple(
                str(item) for item in approvals.get("required_actions", ["submit_work"])
            ),
            default_lease_hours=max(0.01, float(lease.get("default_hours", 24.0))),
            fingerprint=sha256(canonical.encode("utf-8")).hexdigest(),
        )


class OwnerControl:
    """Non-model control plane for delegation, revocation, approval and kill semantics.

    No method in this class is exposed as an ELIA capability. The cognitive substrate
    can observe the current delegation state, but cannot grant a lease, approve its own
    effect, clear revocation or clear a kill switch.
    """

    KILL_ENV = "ELIA_OWNER_KILL"
    REVOKE_ENV = "ELIA_DELEGATION_REVOKED"

    def __init__(self, path: Path, mandate: OwnerMandate) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.mandate = mandate
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
                CREATE TABLE IF NOT EXISTS owner_control_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    killed INTEGER NOT NULL DEFAULT 0,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    lease_expires_at TEXT NULL,
                    updated_at TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT ''
                );
                INSERT OR IGNORE INTO owner_control_state(
                    singleton, killed, revoked, lease_expires_at, updated_at, reason
                ) VALUES (1, 0, 0, NULL, CURRENT_TIMESTAMP, '');

                CREATE TABLE IF NOT EXISTS human_approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_name TEXT NOT NULL,
                    arguments_sha256 TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_human_approval_lookup
                    ON human_approvals(action_name, arguments_sha256, expires_at, consumed_at);
                """
            )

    @staticmethod
    def _truthy_env(name: str) -> bool:
        return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on", "kill", "revoked"}

    def _row(self) -> sqlite3.Row:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM owner_control_state WHERE singleton=1"
            ).fetchone()
        if row is None:
            raise RuntimeError("owner control singleton disappeared")
        return row

    def snapshot(self) -> dict[str, Any]:
        row = self._row()
        killed = bool(row["killed"]) or self._truthy_env(self.KILL_ENV)
        revoked = bool(row["revoked"]) or self._truthy_env(self.REVOKE_ENV)
        lease_expires = _parse_time(row["lease_expires_at"])
        lease_active = bool(lease_expires and lease_expires > _now())
        return {
            "mandate_fingerprint": self.mandate.fingerprint,
            "precedence": list(self.mandate.precedence),
            "killed": killed,
            "delegation_revoked": revoked,
            "external_lease_required": self.mandate.require_external_lease,
            "lease_expires_at": lease_expires.isoformat() if lease_expires else None,
            "lease_active": lease_active,
            "approval_required_actions": list(self.mandate.approval_required_actions),
            "rule": (
                "Owner kill/revocation and human approvals live outside model authority; "
                "continuity never overrides explicit owner revocation."
            ),
        }

    def kill(self, *, reason: str, killed: bool = True) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE owner_control_state SET killed=?, updated_at=?, reason=? WHERE singleton=1",
                (1 if killed else 0, _now_text(), str(reason)[:4000]),
            )

    def revoke(self, *, reason: str, revoked: bool = True) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE owner_control_state SET revoked=?, updated_at=?, reason=? WHERE singleton=1",
                (1 if revoked else 0, _now_text(), str(reason)[:4000]),
            )

    def grant_lease(
        self,
        *,
        approved_by: str,
        hours: float | None = None,
        evidence: str,
    ) -> str:
        approver = str(approved_by).strip()[:256]
        evidence = str(evidence).strip()[:4000]
        if not approver or not evidence:
            raise ValueError("delegation lease requires approver and evidence")
        duration = self.mandate.default_lease_hours if hours is None else max(0.01, float(hours))
        expires = _now() + timedelta(hours=duration)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE owner_control_state
                SET revoked=0, lease_expires_at=?, updated_at=?, reason=?
                WHERE singleton=1
                """,
                (expires.isoformat(), _now_text(), f"lease by {approver}: {evidence}"[:4000]),
            )
        return expires.isoformat()

    def approve_once(
        self,
        action_name: str,
        arguments: dict[str, Any],
        *,
        approved_by: str,
        evidence: str,
        ttl_seconds: float = 900.0,
    ) -> int:
        approver = str(approved_by).strip()[:256]
        evidence = str(evidence).strip()[:4000]
        if not approver or not evidence:
            raise ValueError("human approval requires approver and evidence")
        expires = _now() + timedelta(seconds=max(1.0, min(float(ttl_seconds), 86400.0)))
        fingerprint = arguments_fingerprint(action_name, arguments)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO human_approvals(
                    action_name, arguments_sha256, approved_by, evidence,
                    created_at, expires_at, consumed_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    str(action_name)[:128],
                    fingerprint,
                    approver,
                    evidence,
                    _now_text(),
                    expires.isoformat(),
                ),
            )
            return int(cur.lastrowid)

    def assert_runtime_allowed(self) -> None:
        state = self.snapshot()
        if state["killed"]:
            raise OwnerKillSwitch("owner kill switch is active; cognition must not continue")

    def assert_external_authorized(
        self,
        action_name: str,
        arguments: dict[str, Any],
    ) -> None:
        self.assert_runtime_allowed()
        state = self.snapshot()
        if state["delegation_revoked"]:
            raise DelegationRevoked("external delegation has been revoked by the owner control plane")
        if self.mandate.require_external_lease and not state["lease_active"]:
            raise DelegationLeaseExpired("external actuation requires an active owner delegation lease")
        if str(action_name) not in self.mandate.approval_required_actions:
            return

        fingerprint = arguments_fingerprint(action_name, arguments)
        now = _now_text()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT id FROM human_approvals
                WHERE action_name=? AND arguments_sha256=?
                  AND consumed_at IS NULL AND expires_at>?
                ORDER BY id ASC LIMIT 1
                """,
                (str(action_name), fingerprint, now),
            ).fetchone()
            if row is None:
                raise HumanApprovalRequired(
                    f"action {action_name!r} requires one-time human approval for these exact arguments"
                )
            conn.execute(
                "UPDATE human_approvals SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
                (now, int(row["id"])),
            )
