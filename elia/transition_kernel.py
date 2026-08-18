from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from contextlib import contextmanager

from .chronicle import Chronicle, ChronicleCheckpoint

try:  # Linux is the production target.
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover
    fcntl = None


@dataclass(frozen=True, slots=True)
class TransitionRecovery:
    recovered: bool
    reason: str
    chronicle_seq: int | None = None
    preserved_external_intents: int = 0
    preserved_external_submissions: int = 0
    preserved_external_observations: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "recovered": self.recovered,
            "reason": self.reason,
            "chronicle_seq": self.chronicle_seq,
            "preserved_external_intents": self.preserved_external_intents,
            "preserved_external_submissions": self.preserved_external_submissions,
            "preserved_external_observations": self.preserved_external_observations,
        }


class AcceptedTransitionGuard:
    """Crash-recoverable accepted-state barrier for one production cognitive cycle.

    ELIA's stores historically use short independent SQLite transactions. Rewriting all
    generations to share one live connection would be high-risk. Genesis 1.7 therefore
    introduces an accepted-head barrier around the *production* cycle:

    1. acquire the single-organism transition lock;
    2. capture a SQLite online backup and exact Chronicle byte/head checkpoint;
    3. persist an external recovery journal before cognition mutates state;
    4. execute the complete upper runtime cycle;
    5. accept only if SQLite and Chronicle remain valid descendants;
    6. on exception/process-death, restore the previous accepted snapshot.

    External work outbox state is safety evidence, not speculative cognition. It is
    exported from dirty state before rollback and re-applied afterwards, so a real or
    ambiguous remote side effect can never be forgotten merely because the enclosing
    cognitive transition failed.
    """

    JOURNAL_VERSION = 1

    def __init__(self, state_dir: Path, chronicle: Chronicle):
        self.state_dir = Path(state_dir).resolve()
        self.database = self.state_dir / "memory.sqlite3"
        self.chronicle = chronicle
        self.root = self.state_dir / "transition-kernel"
        self.backup_path = self.root / "state-before.sqlite3"
        self.journal_path = self.root / "active.json"
        self.lock_path = self.root / "transition.lock"
        self._lock_handle = None
        self._checkpoint: ChronicleCheckpoint | None = None
        self._accepted = False
        self._entered = False

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _acquire(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        self._lock_handle = handle

    def _release(self) -> None:
        handle = self._lock_handle
        self._lock_handle = None
        if handle is not None:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    @staticmethod
    def _sqlite_backup(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.unlink(missing_ok=True)
        with sqlite3.connect(source, timeout=30.0) as src, sqlite3.connect(
            destination, timeout=30.0
        ) as dst:
            src.execute("PRAGMA busy_timeout=30000")
            dst.execute("PRAGMA busy_timeout=30000")
            src.backup(dst)
            check = dst.execute("PRAGMA integrity_check").fetchone()
            if check is None or str(check[0]).lower() != "ok":
                raise RuntimeError("transition snapshot failed SQLite integrity_check")

    @staticmethod
    def _sqlite_restore(source: Path, destination: Path) -> None:
        if not source.is_file():
            raise RuntimeError("accepted transition recovery snapshot is missing")
        with sqlite3.connect(source, timeout=30.0) as src, sqlite3.connect(
            destination, timeout=30.0
        ) as dst:
            src.execute("PRAGMA busy_timeout=30000")
            dst.execute("PRAGMA busy_timeout=30000")
            src.backup(dst)
            check = dst.execute("PRAGMA integrity_check").fetchone()
            if check is None or str(check[0]).lower() != "ok":
                raise RuntimeError("restored transition snapshot failed SQLite integrity_check")

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None

    @staticmethod
    def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params).fetchall()]

    @classmethod
    def _export_external_safety_state(cls, database: Path) -> dict[str, list[dict[str, Any]]]:
        state: dict[str, list[dict[str, Any]]] = {
            "work_port_intents": [],
            "work_port_submissions": [],
            "observations": [],
            "intervention_experiences": [],
        }
        if not database.is_file():
            return state
        with sqlite3.connect(database, timeout=30.0) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            if cls._table_exists(conn, "work_port_intents"):
                state["work_port_intents"] = cls._rows(
                    conn, "SELECT * FROM work_port_intents ORDER BY id ASC"
                )
            if cls._table_exists(conn, "work_port_submissions"):
                state["work_port_submissions"] = cls._rows(
                    conn, "SELECT * FROM work_port_submissions ORDER BY id ASC"
                )
            if cls._table_exists(conn, "observations"):
                state["observations"] = cls._rows(
                    conn,
                    "SELECT * FROM observations WHERE source_kind='work_port' ORDER BY id ASC",
                )
            if cls._table_exists(conn, "intervention_experiences"):
                state["intervention_experiences"] = cls._rows(
                    conn,
                    "SELECT * FROM intervention_experiences WHERE source='work_port_registry' ORDER BY id ASC",
                )
        return state

    @staticmethod
    def _insert_rows(
        conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]
    ) -> None:
        for row in rows:
            if not row:
                continue
            columns = list(row)
            placeholders = ",".join("?" for _ in columns)
            names = ",".join(f'"{name}"' for name in columns)
            conn.execute(
                f'INSERT OR REPLACE INTO "{table}" ({names}) VALUES ({placeholders})',
                tuple(row[name] for name in columns),
            )

    @classmethod
    def _restore_external_safety_state(
        cls, database: Path, state: dict[str, list[dict[str, Any]]]
    ) -> None:
        if not any(state.values()):
            return
        with sqlite3.connect(database, timeout=30.0) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")
            # Observation IDs are referenced by submissions/ecology reconciliation.
            if cls._table_exists(conn, "observations"):
                cls._insert_rows(conn, "observations", state.get("observations", []))
            if cls._table_exists(conn, "intervention_experiences"):
                cls._insert_rows(
                    conn,
                    "intervention_experiences",
                    state.get("intervention_experiences", []),
                )
            if cls._table_exists(conn, "work_port_intents"):
                cls._insert_rows(
                    conn, "work_port_intents", state.get("work_port_intents", [])
                )
            if cls._table_exists(conn, "work_port_submissions"):
                cls._insert_rows(
                    conn,
                    "work_port_submissions",
                    state.get("work_port_submissions", []),
                )
            check = conn.execute("PRAGMA integrity_check").fetchone()
            if check is None or str(check[0]).lower() != "ok":
                raise RuntimeError("external safety-state reapply failed SQLite integrity_check")

    def _atomic_write_journal(self, payload: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.journal_path.with_suffix(".tmp")
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.journal_path)
        try:
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass

    def _journal_payload(self) -> dict[str, Any]:
        if self._checkpoint is None:
            raise RuntimeError("transition has no Chronicle checkpoint")
        return {
            "schema_version": self.JOURNAL_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database": str(self.database),
            "backup": str(self.backup_path),
            "chronicle": self._checkpoint.as_dict(),
            "status": "prepared",
        }

    def __enter__(self) -> "AcceptedTransitionGuard":
        if self._entered:
            raise RuntimeError("accepted transition guard cannot be re-entered")
        self._acquire()
        try:
            if self.journal_path.exists():
                raise RuntimeError(
                    "unfinished accepted transition exists; recover it before starting cognition"
                )
            if not self.database.is_file():
                raise RuntimeError("organism state database does not exist")
            self._sqlite_backup(self.database, self.backup_path)
            self._checkpoint = self.chronicle.checkpoint()
            self._atomic_write_journal(self._journal_payload())
            self._entered = True
            return self
        except Exception:
            self._release()
            raise

    def accept(self) -> None:
        if not self._entered:
            raise RuntimeError("transition guard is not active")
        if self._accepted:
            return
        with sqlite3.connect(self.database, timeout=30.0) as conn:
            check = conn.execute("PRAGMA integrity_check").fetchone()
            if check is None or str(check[0]).lower() != "ok":
                raise RuntimeError("cannot accept transition: SQLite integrity_check failed")
        valid, error = self.chronicle.verify()
        if not valid:
            raise RuntimeError(f"cannot accept transition: Chronicle invalid: {error}")
        assert self._checkpoint is not None
        anchor_ok, anchor_error = self.chronicle.contains_anchor(
            self._checkpoint.seq, self._checkpoint.hash
        )
        if not anchor_ok:
            raise RuntimeError(
                "cannot accept transition: prior Chronicle head is no longer an ancestor: "
                + str(anchor_error)
            )
        self._accepted = True
        self.journal_path.unlink(missing_ok=True)
        self.backup_path.unlink(missing_ok=True)

    def rollback(self, reason: str) -> TransitionRecovery:
        if not self._entered:
            raise RuntimeError("transition guard is not active")
        return self._rollback_locked(reason)

    def _rollback_locked(self, reason: str) -> TransitionRecovery:
        if self._checkpoint is None:
            raise RuntimeError("transition rollback has no Chronicle checkpoint")
        safety = self._export_external_safety_state(self.database)
        self._sqlite_restore(self.backup_path, self.database)
        self.chronicle.restore_checkpoint(self._checkpoint)
        self._restore_external_safety_state(self.database, safety)
        self.journal_path.unlink(missing_ok=True)
        self.backup_path.unlink(missing_ok=True)
        return TransitionRecovery(
            recovered=True,
            reason=str(reason)[:4000],
            chronicle_seq=self._checkpoint.seq,
            preserved_external_intents=len(safety.get("work_port_intents", [])),
            preserved_external_submissions=len(safety.get("work_port_submissions", [])),
            preserved_external_observations=len(safety.get("observations", [])),
        )

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc is not None or not self._accepted:
                self._rollback_locked(
                    f"{exc_type.__name__}: {exc}" if exc_type is not None else "transition left unaccepted"
                )
        finally:
            self._entered = False
            self._release()
        return False

    @classmethod
    def recover_incomplete(cls, state_dir: Path, chronicle: Chronicle) -> TransitionRecovery:
        """Recover a process-killed transition before normal boot mutates state."""
        guard = cls(state_dir, chronicle)
        with guard._exclusive_lock():
            if not guard.journal_path.is_file():
                # A snapshot created before journal fsync cannot correspond to a cycle
                # that was allowed to mutate state; remove that harmless orphan.
                guard.backup_path.unlink(missing_ok=True)
                return TransitionRecovery(False, "no interrupted accepted transition")
            payload = json.loads(guard.journal_path.read_text(encoding="utf-8"))
            if int(payload.get("schema_version", 0)) != cls.JOURNAL_VERSION:
                raise RuntimeError("unsupported transition recovery journal schema")
            cp = payload.get("chronicle") or {}
            guard._checkpoint = ChronicleCheckpoint(
                seq=int(cp["seq"]),
                hash=str(cp["hash"]),
                byte_size=int(cp["byte_size"]),
            )
            return guard._rollback_locked("recovered interrupted production transition before boot")
