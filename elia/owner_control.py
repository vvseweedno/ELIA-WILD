from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from importlib import import_module
import json
import math
import os
from pathlib import Path
import sqlite3
from types import ModuleType
from typing import Any, Iterator
from uuid import uuid4

import yaml

from .body.types import bounded_json_value
from .sqlite_utils import inserted_row_id
from .transition_kernel import StateWriterLock, fsync_directory

fcntl: ModuleType | None = None
try:  # Linux is the production target.
    fcntl = import_module("fcntl")
except ImportError:  # pragma: no cover
    pass


OWNER_SIGNAL_VERSION = 1


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
    bounded = bounded_json_value(
        value,
        field="owner authority value",
        max_bytes=512_000,
        max_depth=12,
        max_items=4096,
    )
    return json.dumps(
        bounded,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def arguments_fingerprint(action_name: str, arguments: dict[str, Any]) -> str:
    if not isinstance(action_name, str) or not action_name or len(action_name) > 128:
        raise ValueError("approval action_name must be a non-empty bounded string")
    if not isinstance(arguments, dict):
        raise ValueError("approval arguments must be a JSON object")
    return sha256(_canonical([action_name, arguments]).encode("utf-8")).hexdigest()


def owner_signal_path(database: Path) -> Path:
    """Return the owner signal outside the replaceable organism state directory."""

    resolved = Path(database).resolve()
    state_dir = resolved.parent
    return state_dir.parent / f".{state_dir.name}.owner-control.json"


def _owner_signal_lock_path(database: Path) -> Path:
    signal = owner_signal_path(database)
    return signal.with_name(f"{signal.name}.lock")


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "kill",
        "revoked",
    }


@contextmanager
def _signal_lock(database: Path) -> Iterator[None]:
    if fcntl is None:  # pragma: no cover - Linux is the production contract.
        raise RuntimeError("owner control cross-process locking requires fcntl")
    path = _owner_signal_lock_path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_signal(database: Path, *, fail_closed: bool) -> dict[str, Any] | None:
    path = owner_signal_path(database)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("expected JSON object")
        if int(payload.get("schema_version", 0)) != OWNER_SIGNAL_VERSION:
            raise ValueError("unsupported schema")
        generation = int(payload.get("generation", -1))
        if generation < 0:
            raise ValueError("invalid generation")
        return {
            "schema_version": OWNER_SIGNAL_VERSION,
            "generation": generation,
            "killed": bool(payload.get("killed", False)),
            "revoked": bool(payload.get("revoked", False)),
            "updated_at": str(payload.get("updated_at") or ""),
            "reason": str(payload.get("reason") or ""),
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        if fail_closed:
            return {
                "schema_version": OWNER_SIGNAL_VERSION,
                "generation": 0,
                "killed": True,
                "revoked": True,
                "updated_at": "",
                "reason": "owner control sidecar is invalid; fail closed",
            }
        raise OwnerControlError("owner control sidecar is invalid")


def _write_signal(database: Path, payload: dict[str, Any]) -> None:
    path = owner_signal_path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        os.chmod(temp, 0o600)
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    fsync_directory(path.parent)


def owner_kill_active(database: Path) -> bool:
    """Cheap, non-mutating kill check safe before state initialization/recovery."""

    if _truthy_env(OwnerControl.KILL_ENV):
        return True
    signal = _read_signal(database, fail_closed=True)
    if signal is not None and bool(signal["killed"]):
        return True
    path = Path(database)
    if not path.is_file():
        return False
    try:
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT killed FROM owner_control_state WHERE singleton=1"
            ).fetchone()
            return bool(row and row[0])
    except sqlite3.Error:
        # Absence of the optional legacy table is not a kill. Any sidecar corruption
        # already failed closed above, and lifecycle performs separate DB integrity.
        return False


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
        canonical = _canonical(
            raw
            if raw
            else {
                "schema_version": 1,
                "precedence": list(precedence),
                "delegation_lease": {
                    "require_for_external_effects": True,
                    "default_hours": 24,
                },
                "human_approval": {"required_actions": ["submit_work"]},
            }
        )
        default_lease_hours = float(lease.get("default_hours", 24.0))
        if not math.isfinite(default_lease_hours) or default_lease_hours <= 0:
            raise ValueError("owner mandate default lease hours must be finite and positive")
        return cls(
            schema_version=int(raw.get("schema_version", 1)),
            precedence=precedence,
            require_external_lease=bool(
                lease.get("require_for_external_effects", True)
            ),
            approval_required_actions=tuple(
                str(item) for item in approvals.get("required_actions", ["submit_work"])
            ),
            default_lease_hours=max(0.01, default_lease_hours),
            fingerprint=sha256(canonical.encode("utf-8")).hexdigest(),
        )


class OwnerControl:
    """Non-model control plane for delegation, revocation, approval and kill semantics.

    No method in this class is exposed as an ELIA capability. The cognitive substrate
    can observe the current delegation state, but cannot grant a lease, approve its own
    effect, clear revocation or clear a kill switch.

    The out-of-state sidecar is a fail-safe durability channel, not a cryptographic
    witness: it is created mode 0600 and trusts the host account/filesystem boundary.
    A process with arbitrary write authority as the same OS user can forge it, so the
    production body must not delegate such authority to cognition. Malformed sidecars
    fail closed as killed+revoked; remotely portable rollback trust remains the HMAC-
    authenticated checkpoint/wake-anchor responsibility.
    """

    KILL_ENV = "ELIA_OWNER_KILL"
    REVOKE_ENV = "ELIA_DELEGATION_REVOKED"

    def __init__(self, path: Path, mandate: OwnerMandate) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.mandate = mandate
        self._init_db()
        self._initialize_signal()

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
        return _truthy_env(name)

    def _initialize_signal(self) -> None:
        """Migrate legacy DB authority once, without clearing an existing sidecar."""

        with _signal_lock(self.path):
            if owner_signal_path(self.path).exists():
                # Validate eagerly. Invalid control evidence is a hard failure.
                _read_signal(self.path, fail_closed=False)
                return
            row = self._row()
            _write_signal(
                self.path,
                {
                    "schema_version": OWNER_SIGNAL_VERSION,
                    "generation": 0,
                    "killed": bool(row["killed"]),
                    "revoked": bool(row["revoked"]),
                    "updated_at": _now_text(),
                    "reason": "migrated from owner-control database",
                },
            )

    def _update_signal_locked(
        self,
        *,
        killed: bool | None = None,
        revoked: bool | None = None,
        reason: str,
    ) -> None:
        current = _read_signal(self.path, fail_closed=False) or {
            "generation": 0,
            "killed": False,
            "revoked": False,
        }
        _write_signal(
            self.path,
            {
                "schema_version": OWNER_SIGNAL_VERSION,
                "generation": int(current.get("generation", 0)) + 1,
                "killed": bool(current["killed"] if killed is None else killed),
                "revoked": bool(current["revoked"] if revoked is None else revoked),
                "updated_at": _now_text(),
                "reason": str(reason)[:4000],
            },
        )

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
        signal = _read_signal(self.path, fail_closed=True) or {}
        killed = (
            bool(row["killed"])
            or bool(signal.get("killed", False))
            or self._truthy_env(self.KILL_ENV)
        )
        revoked = (
            bool(row["revoked"])
            or bool(signal.get("revoked", False))
            or self._truthy_env(self.REVOKE_ENV)
        )
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
        reason = str(reason)[:4000]
        if killed:
            # Publish stop authority first, but release the signal lock before waiting
            # for the organism writer. Constructors hold writer->signal while booting;
            # retaining signal->writer here would deadlock that fail-safe path.
            with _signal_lock(self.path):
                self._update_signal_locked(killed=True, reason=reason)
            with StateWriterLock(self.path.parent):
                # Reassert after serialization with a concurrent DB-first clear. This
                # makes the sidecar and database agree when both calls return.
                with _signal_lock(self.path):
                    self._update_signal_locked(killed=True, reason=reason)
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE owner_control_state SET killed=?, updated_at=?, reason=? WHERE singleton=1",
                        (1, _now_text(), reason),
                    )
            return

        # Clearing is deliberately DB-first and writer->signal. A crash can leave a
        # false positive stop, never silently clear a durable owner stop request.
        with StateWriterLock(self.path.parent):
            with self._connect() as conn:
                conn.execute(
                    "UPDATE owner_control_state SET killed=0, updated_at=?, reason=? WHERE singleton=1",
                    (_now_text(), reason),
                )
            with _signal_lock(self.path):
                self._update_signal_locked(killed=False, reason=reason)

    def revoke(self, *, reason: str, revoked: bool = True) -> None:
        reason = str(reason)[:4000]
        if revoked:
            with _signal_lock(self.path):
                self._update_signal_locked(revoked=True, reason=reason)
            with StateWriterLock(self.path.parent):
                with _signal_lock(self.path):
                    self._update_signal_locked(revoked=True, reason=reason)
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE owner_control_state SET revoked=?, updated_at=?, reason=? WHERE singleton=1",
                        (1, _now_text(), reason),
                    )
            return

        with StateWriterLock(self.path.parent):
            with self._connect() as conn:
                conn.execute(
                    "UPDATE owner_control_state SET revoked=0, updated_at=?, reason=? WHERE singleton=1",
                    (_now_text(), reason),
                )
            with _signal_lock(self.path):
                self._update_signal_locked(revoked=False, reason=reason)

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
        duration = self.mandate.default_lease_hours if hours is None else float(hours)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("delegation lease hours must be finite and positive")
        duration = max(0.01, duration)
        expires = _now() + timedelta(hours=duration)
        reason = f"lease by {approver}: {evidence}"[:4000]
        with StateWriterLock(self.path.parent):
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE owner_control_state
                    SET revoked=0, lease_expires_at=?, updated_at=?, reason=?
                    WHERE singleton=1
                    """,
                    (expires.isoformat(), _now_text(), reason),
                )
            with _signal_lock(self.path):
                self._update_signal_locked(revoked=False, reason=reason)
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
        ttl = float(ttl_seconds)
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("human approval ttl_seconds must be finite and positive")
        expires = _now() + timedelta(seconds=max(1.0, min(ttl, 86400.0)))
        fingerprint = arguments_fingerprint(action_name, arguments)
        with StateWriterLock(self.path.parent):
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO human_approvals(
                        action_name, arguments_sha256, approved_by, evidence,
                        created_at, expires_at, consumed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        action_name,
                        fingerprint,
                        approver,
                        evidence,
                        _now_text(),
                        expires.isoformat(),
                    ),
                )
                return inserted_row_id(cur, operation="human approval insert")

    def assert_runtime_allowed(self) -> None:
        state = self.snapshot()
        if state["killed"]:
            raise OwnerKillSwitch(
                "owner kill switch is active; cognition must not continue"
            )

    def assert_external_authorized(
        self,
        action_name: str,
        arguments: dict[str, Any],
    ) -> None:
        with StateWriterLock(self.path.parent):
            self.assert_runtime_allowed()
            state = self.snapshot()
            if state["delegation_revoked"]:
                raise DelegationRevoked(
                    "external delegation has been revoked by the owner control plane"
                )
            if self.mandate.require_external_lease and not state["lease_active"]:
                raise DelegationLeaseExpired(
                    "external actuation requires an active owner delegation lease"
                )
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
