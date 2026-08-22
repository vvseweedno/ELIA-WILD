from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import threading
import time
from types import ModuleType
from typing import Any, BinaryIO, Iterator, Literal
from uuid import uuid4

from .chronicle import Chronicle, ChronicleCheckpoint


MAX_WORKSPACE_MEMBERS = 4096
MAX_WORKSPACE_FILE_BYTES = 256 * 1024 * 1024
MAX_WORKSPACE_TOTAL_BYTES = 768 * 1024 * 1024

fcntl: ModuleType | None = None
try:  # Linux is the production target.
    fcntl = import_module("fcntl")
except ImportError:  # pragma: no cover
    pass


class StateWriterLockTimeout(RuntimeError):
    """A second process could not obtain the organism-wide mutation lease in time."""


_writer_local = threading.local()


def _thread_writer_depths() -> dict[str, int]:
    """Per-thread reentrancy registry, reset defensively after ``fork()``."""

    pid = os.getpid()
    if getattr(_writer_local, "pid", None) != pid:
        _writer_local.pid = pid
        _writer_local.depths = {}
    return _writer_local.depths


def state_writer_lock_path(state_dir: Path) -> Path:
    """Return a lock path that survives atomic replacement of ``state_dir`` itself."""

    resolved = Path(state_dir).resolve()
    return resolved.parent / f".{resolved.name}.writer.lock"


def fsync_directory(path: Path) -> None:
    """Durably publish directory-entry changes on the Linux production target."""

    descriptor = os.open(Path(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class StateWriterLock:
    """Cross-process serialization for accepted transitions and checkpoint mutation.

    The lock deliberately lives beside the state directory, not inside it. Checkpoint
    restore atomically replaces the state directory, so an in-tree inode would allow a
    pre-restore writer and a post-restore writer to hold two different "exclusive" locks.
    """

    def __init__(self, state_dir: Path, *, timeout_seconds: float = 30.0) -> None:
        self.state_dir = Path(state_dir).resolve()
        self.path = state_writer_lock_path(self.state_dir)
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self._handle: BinaryIO | None = None
        self._reentrant = False

    def acquire(self) -> None:
        if self._handle is not None or self._reentrant:
            raise RuntimeError("organism writer lock is already held by this object")
        depths = _thread_writer_depths()
        key = str(self.path)
        if depths.get(key, 0) > 0:
            depths[key] += 1
            self._reentrant = True
            return
        if fcntl is None:  # pragma: no cover - Linux is the production contract.
            raise RuntimeError("cross-process organism writer locking requires fcntl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise StateWriterLockTimeout(
                            f"organism writer lock remained busy for {self.timeout_seconds:.3f}s: "
                            f"{self.path}"
                        ) from exc
                    time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))
            handle.seek(0)
            handle.truncate()
            handle.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "acquired_at": datetime.now(timezone.utc).isoformat(),
                    },
                    sort_keys=True,
                ).encode("utf-8")
            )
            handle.flush()
            os.fsync(handle.fileno())
            self._handle = handle
            depths[key] = 1
        except Exception:
            handle.close()
            raise

    def release(self) -> None:
        depths = _thread_writer_depths()
        key = str(self.path)
        if self._reentrant:
            depth = depths.get(key, 0)
            if depth < 2:
                raise RuntimeError("organism writer reentrancy depth is inconsistent")
            depths[key] = depth - 1
            self._reentrant = False
            return
        handle = self._handle
        if handle is None:
            return
        if depths.get(key, 0) != 1:
            raise RuntimeError(
                "outer organism writer lock cannot be released before nested leases"
            )
        if fcntl is None:  # pragma: no cover - defensive optimized-mode invariant.
            raise RuntimeError("cannot release organism writer lock without fcntl")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        self._handle = None
        depths.pop(key, None)
        handle.close()

    def __enter__(self) -> "StateWriterLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        self.release()
        return False


@dataclass(frozen=True, slots=True)
class TransitionRecovery:
    recovered: bool
    reason: str
    chronicle_seq: int | None = None
    preserved_external_intents: int = 0
    preserved_external_submissions: int = 0
    preserved_external_observations: int = 0
    preserved_external_effects: int = 0
    preserved_owner_controls: int = 0
    preserved_resource_ingress: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "recovered": self.recovered,
            "reason": self.reason,
            "chronicle_seq": self.chronicle_seq,
            "preserved_external_intents": self.preserved_external_intents,
            "preserved_external_submissions": self.preserved_external_submissions,
            "preserved_external_observations": self.preserved_external_observations,
            "preserved_external_effects": self.preserved_external_effects,
            "preserved_owner_controls": self.preserved_owner_controls,
            "preserved_resource_ingress": self.preserved_resource_ingress,
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

    External truth is not speculative cognition. Durable WorkPort outbox evidence,
    universal external-effect intents, owner kill/revocation/approval state and verified
    resource-ingress evidence are exported from dirty state before rollback and
    re-applied afterwards. If an effect crossed the external boundary inside a transition
    that later rolls back, that effect is deliberately reclassified as ``indeterminate``:
    even a successful adapter response is not permission to repeat an effect whose local
    causal consequences were not accepted.
    """

    JOURNAL_VERSION = 1
    _CROSSED_EFFECT_STATUSES = frozenset({"sending", "succeeded", "reconciled_effect"})

    def __init__(self, state_dir: Path, chronicle: Chronicle):
        self.state_dir = Path(state_dir).resolve()
        self.database = self.state_dir / "memory.sqlite3"
        self.chronicle = chronicle
        self.root = self.state_dir / "transition-kernel"
        self.backup_path = self.root / "state-before.sqlite3"
        self.workspace_backup_path = self.root / "workspace-before"
        self.safety_path = self.root / "rollback-safety.json"
        self.journal_path = self.root / "active.json"
        self.lock_path = state_writer_lock_path(self.state_dir)
        self._writer_lock: StateWriterLock | None = None
        self._checkpoint: ChronicleCheckpoint | None = None
        self._workspace_existed = False
        self._accepted = False
        self._entered = False

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        with StateWriterLock(self.state_dir):
            yield

    def _acquire(self) -> None:
        lock = StateWriterLock(self.state_dir)
        lock.acquire()
        try:
            root_existed = self.root.exists()
            self.root.mkdir(parents=True, exist_ok=True)
            if not root_existed:
                fsync_directory(self.state_dir)
            self._writer_lock = lock
        except Exception:
            lock.release()
            raise

    def _release(self) -> None:
        lock = self._writer_lock
        self._writer_lock = None
        if lock is not None:
            lock.release()

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
            dst.commit()
            dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            dst.execute("PRAGMA journal_mode=DELETE")
            dst.commit()
            check = dst.execute("PRAGMA integrity_check").fetchone()
            if check is None or str(check[0]).lower() != "ok":
                raise RuntimeError("transition snapshot failed SQLite integrity_check")
        for suffix in ("-wal", "-shm"):
            destination.with_name(destination.name + suffix).unlink(missing_ok=True)
        with destination.open("rb") as handle:
            os.fsync(handle.fileno())
        fsync_directory(destination.parent)

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
            dst.execute("PRAGMA wal_checkpoint(FULL)")
        for candidate in (
            destination,
            destination.with_name(destination.name + "-wal"),
            destination.with_name(destination.name + "-shm"),
        ):
            if candidate.is_file():
                with candidate.open("rb") as handle:
                    os.fsync(handle.fileno())
        fsync_directory(destination.parent)

    @staticmethod
    def _remove_tree(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)

    @staticmethod
    def _stat_signature(item: os.stat_result) -> tuple[int, ...]:
        """Fields that must remain stable while an accepted snapshot is captured."""

        return (
            int(item.st_dev),
            int(item.st_ino),
            int(item.st_mode),
            int(item.st_nlink),
            int(item.st_size),
            int(item.st_mtime_ns),
            int(item.st_ctime_ns),
        )

    @classmethod
    def _inventory_workspace_fd(
        cls,
        root_fd: int,
    ) -> tuple[
        dict[str, tuple[str, int, int, int, str]],
        dict[str, tuple[int, ...]],
    ]:
        """Build a stable, bounded, no-follow workspace inventory before copying.

        A transition snapshot is a rollback authority, so silently dereferencing links,
        copying special files, or expanding a hard-linked inode is not acceptable. The
        complete inventory is collected before the destination directory is created;
        known oversize workspaces therefore fail without duplicating their contents.
        """

        semantic: dict[str, tuple[str, int, int, int, str]] = {}
        signatures: dict[str, tuple[int, ...]] = {}
        members = 0
        total_bytes = 0

        def scan(directory_fd: int, relative: Path) -> None:
            nonlocal members, total_bytes
            before_directory = os.fstat(directory_fd)
            relative_name = relative.as_posix() if relative.parts else "."
            signatures[relative_name] = cls._stat_signature(before_directory)
            semantic[relative_name] = (
                "directory",
                stat.S_IMODE(before_directory.st_mode),
                0,
                int(before_directory.st_mtime_ns),
                "",
            )
            for name in sorted(os.listdir(directory_fd)):
                child_relative = relative / name
                child_name = child_relative.as_posix()
                item = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                members += 1
                if members > MAX_WORKSPACE_MEMBERS:
                    raise RuntimeError(
                        "workspace exceeds the accepted-state member limit "
                        f"({MAX_WORKSPACE_MEMBERS})"
                    )
                if stat.S_ISLNK(item.st_mode):
                    raise RuntimeError(
                        "workspace symlink cannot cross the accepted-state boundary: "
                        f"{child_name}"
                    )
                signature = cls._stat_signature(item)
                signatures[child_name] = signature
                if stat.S_ISDIR(item.st_mode):
                    child_fd = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                    try:
                        if cls._stat_signature(os.fstat(child_fd)) != signature:
                            raise RuntimeError(
                                f"workspace directory changed during snapshot preflight: {child_name}"
                            )
                        scan(child_fd, child_relative)
                        if cls._stat_signature(os.fstat(child_fd)) != signature:
                            raise RuntimeError(
                                f"workspace directory changed during snapshot preflight: {child_name}"
                            )
                    finally:
                        os.close(child_fd)
                    continue
                if not stat.S_ISREG(item.st_mode):
                    raise RuntimeError(
                        "workspace special file cannot cross the accepted-state boundary: "
                        f"{child_name}"
                    )
                if item.st_nlink != 1:
                    raise RuntimeError(
                        "workspace hard-linked file cannot cross the accepted-state boundary: "
                        f"{child_name}"
                    )
                size = int(item.st_size)
                if size > MAX_WORKSPACE_FILE_BYTES:
                    raise RuntimeError(
                        "workspace file exceeds the accepted-state size limit: "
                        f"{child_name} ({size} bytes)"
                    )
                total_bytes += size
                if total_bytes > MAX_WORKSPACE_TOTAL_BYTES:
                    raise RuntimeError(
                        "workspace exceeds the accepted-state total byte limit "
                        f"({MAX_WORKSPACE_TOTAL_BYTES})"
                    )
                source_fd = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
                try:
                    if cls._stat_signature(os.fstat(source_fd)) != signature:
                        raise RuntimeError(
                            f"workspace file changed during snapshot preflight: {child_name}"
                        )
                    digest = sha256()
                    bytes_read = 0
                    while True:
                        chunk = os.read(source_fd, 1024 * 1024)
                        if not chunk:
                            break
                        bytes_read += len(chunk)
                        if bytes_read > size:
                            raise RuntimeError(
                                f"workspace file grew during snapshot preflight: {child_name}"
                            )
                        digest.update(chunk)
                    if bytes_read != size or cls._stat_signature(os.fstat(source_fd)) != signature:
                        raise RuntimeError(
                            f"workspace file changed during snapshot preflight: {child_name}"
                        )
                finally:
                    os.close(source_fd)
                semantic[child_name] = (
                    "file",
                    stat.S_IMODE(item.st_mode),
                    size,
                    int(item.st_mtime_ns),
                    digest.hexdigest(),
                )
            if cls._stat_signature(os.fstat(directory_fd)) != cls._stat_signature(
                before_directory
            ):
                raise RuntimeError(
                    f"workspace directory changed during snapshot preflight: {relative_name}"
                )

        scan(root_fd, Path())
        return semantic, signatures

    @classmethod
    def _copy_tree_durable(cls, source: Path, destination: Path) -> None:
        source = Path(source)
        destination = Path(destination)
        if source.is_symlink():
            raise RuntimeError(f"workspace snapshot source is not a real directory: {source}")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            root_fd = os.open(source, flags)
        except OSError as exc:
            raise RuntimeError(
                f"workspace snapshot source is not a real directory: {source}"
            ) from exc
        destination_created = False
        try:
            expected, signatures = cls._inventory_workspace_fd(root_fd)
            cls._remove_tree(destination)
            root_stat = os.fstat(root_fd)
            destination.mkdir(parents=True, mode=0o700)
            destination_created = True
            copied_total = 0

            def copy_directory(directory_fd: int, target: Path, relative: Path) -> None:
                nonlocal copied_total
                directory_name = relative.as_posix() if relative.parts else "."
                if cls._stat_signature(os.fstat(directory_fd)) != signatures[directory_name]:
                    raise RuntimeError(
                        f"workspace directory changed while snapshotting: {directory_name}"
                    )
                for name in sorted(os.listdir(directory_fd)):
                    child_relative = relative / name
                    child_name = child_relative.as_posix()
                    item = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if cls._stat_signature(item) != signatures.get(child_name):
                        raise RuntimeError(
                            f"workspace entry changed while snapshotting: {child_name}"
                        )
                    child_target = target / name
                    if stat.S_ISDIR(item.st_mode):
                        child_target.mkdir(mode=0o700)
                        child_fd = os.open(
                            name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory_fd,
                        )
                        try:
                            copy_directory(child_fd, child_target, child_relative)
                        finally:
                            os.close(child_fd)
                        os.chmod(child_target, stat.S_IMODE(item.st_mode))
                        os.utime(
                            child_target,
                            ns=(int(item.st_atime_ns), int(item.st_mtime_ns)),
                            follow_symlinks=False,
                        )
                        fsync_directory(child_target)
                        continue
                    if not stat.S_ISREG(item.st_mode) or item.st_nlink != 1:
                        raise RuntimeError(
                            f"workspace entry changed to an unsafe type: {child_name}"
                        )
                    source_fd = os.open(
                        name,
                        os.O_RDONLY | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                    try:
                        if cls._stat_signature(os.fstat(source_fd)) != signatures[child_name]:
                            raise RuntimeError(
                                f"workspace file changed while snapshotting: {child_name}"
                            )
                        digest = sha256()
                        file_bytes = 0
                        with child_target.open("xb") as output:
                            os.chmod(child_target, 0o600)
                            while True:
                                chunk = os.read(source_fd, 1024 * 1024)
                                if not chunk:
                                    break
                                file_bytes += len(chunk)
                                copied_total += len(chunk)
                                if (
                                    file_bytes > MAX_WORKSPACE_FILE_BYTES
                                    or copied_total > MAX_WORKSPACE_TOTAL_BYTES
                                ):
                                    raise RuntimeError(
                                        "workspace changed beyond accepted-state byte limits "
                                        f"while snapshotting: {child_name}"
                                    )
                                output.write(chunk)
                                digest.update(chunk)
                            output.flush()
                            os.fsync(output.fileno())
                        expected_file = expected[child_name]
                        if (
                            file_bytes != expected_file[2]
                            or digest.hexdigest() != expected_file[4]
                            or cls._stat_signature(os.fstat(source_fd))
                            != signatures[child_name]
                        ):
                            raise RuntimeError(
                                f"workspace file changed while snapshotting: {child_name}"
                            )
                    finally:
                        os.close(source_fd)
                    os.chmod(child_target, stat.S_IMODE(item.st_mode))
                    os.utime(
                        child_target,
                        ns=(int(item.st_atime_ns), int(item.st_mtime_ns)),
                        follow_symlinks=False,
                    )
                if cls._stat_signature(os.fstat(directory_fd)) != signatures[directory_name]:
                    raise RuntimeError(
                        f"workspace directory changed while snapshotting: {directory_name}"
                    )

            copy_directory(root_fd, destination, Path())
            os.chmod(destination, stat.S_IMODE(root_stat.st_mode))
            os.utime(
                destination,
                ns=(int(root_stat.st_atime_ns), int(root_stat.st_mtime_ns)),
                follow_symlinks=False,
            )
            fsync_directory(destination)
        except Exception:
            if destination_created:
                cls._remove_tree(destination)
                fsync_directory(destination.parent)
            raise
        finally:
            os.close(root_fd)

    def _snapshot_workspace(self) -> None:
        workspace = self.state_dir / "workspace"
        self._remove_tree(self.workspace_backup_path)
        if workspace.is_symlink():
            raise RuntimeError(
                "workspace snapshot source is not a real directory: " + str(workspace)
            )
        self._workspace_existed = workspace.exists()
        if not self._workspace_existed:
            return
        self._copy_tree_durable(workspace, self.workspace_backup_path)
        fsync_directory(self.root)

    def _restore_workspace(self) -> None:
        workspace = self.state_dir / "workspace"
        token = uuid4().hex
        staging = self.state_dir / f".workspace-restore-{token}"
        quarantine = self.state_dir / f".workspace-rolled-back-{token}"
        self._remove_tree(staging)
        try:
            if self._workspace_existed:
                if not self.workspace_backup_path.is_dir():
                    raise RuntimeError("accepted transition workspace snapshot is missing")
                self._copy_tree_durable(self.workspace_backup_path, staging)
            if workspace.exists() or workspace.is_symlink():
                os.replace(workspace, quarantine)
                fsync_directory(self.state_dir)
            if self._workspace_existed:
                os.replace(staging, workspace)
                fsync_directory(self.state_dir)
            self._remove_tree(quarantine)
            fsync_directory(self.state_dir)
        except Exception:
            self._remove_tree(staging)
            if quarantine.exists() and not workspace.exists():
                os.replace(quarantine, workspace)
                fsync_directory(self.state_dir)
            raise

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
            "external_effect_intents": [],
            "owner_control_state": [],
            "human_approvals": [],
            "resource_ingress_events": [],
            "resource_events": [],
            "verification_receipt_consumptions": [],
            "ecology_work_items": [],
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
                    """
                    SELECT * FROM observations
                    WHERE source_kind IN ('work_port','resource_ingress')
                    ORDER BY id ASC
                    """,
                )
            if cls._table_exists(conn, "intervention_experiences"):
                state["intervention_experiences"] = cls._rows(
                    conn,
                    """
                    SELECT * FROM intervention_experiences
                    WHERE source IN ('work_port_registry','resource_ingress_registry')
                    ORDER BY id ASC
                    """,
                )
            if cls._table_exists(conn, "external_effect_intents"):
                state["external_effect_intents"] = cls._rows(
                    conn, "SELECT * FROM external_effect_intents ORDER BY id ASC"
                )
            if cls._table_exists(conn, "owner_control_state"):
                state["owner_control_state"] = cls._rows(
                    conn, "SELECT * FROM owner_control_state ORDER BY singleton ASC"
                )
            if cls._table_exists(conn, "human_approvals"):
                state["human_approvals"] = cls._rows(
                    conn, "SELECT * FROM human_approvals ORDER BY id ASC"
                )
            if cls._table_exists(conn, "resource_events"):
                state["resource_events"] = cls._rows(
                    conn,
                    "SELECT * FROM resource_events WHERE source LIKE 'ingress:%' ORDER BY id ASC",
                )
            if cls._table_exists(conn, "verification_receipt_consumptions"):
                state["verification_receipt_consumptions"] = cls._rows(
                    conn,
                    """
                    SELECT * FROM verification_receipt_consumptions
                    WHERE purpose='economy.resource_event'
                    ORDER BY consumed_at ASC, authority ASC, nonce ASC
                    """,
                )
            if cls._table_exists(conn, "ecology_work_items"):
                state["ecology_work_items"] = cls._rows(
                    conn,
                    """
                    SELECT * FROM ecology_work_items
                    WHERE resource_event_id IN (
                        SELECT id FROM resource_events WHERE source LIKE 'ingress:%'
                    )
                    ORDER BY id ASC
                    """,
                )
            if cls._table_exists(conn, "resource_ingress_events"):
                state["resource_ingress_events"] = cls._rows(
                    conn, "SELECT * FROM resource_ingress_events ORDER BY id ASC"
                )
        return state

    @classmethod
    def _quarantine_rolled_back_effects(
        cls,
        dirty: dict[str, list[dict[str, Any]]],
        accepted_before: dict[str, list[dict[str, Any]]],
    ) -> None:
        """Make effects advanced only in the failed transition non-repeatable.

        Rows that already existed unchanged in the accepted snapshot keep their prior
        status. A new/changed row that crossed the remote boundary becomes
        ``indeterminate`` because local state is about to roll back behind that effect.
        ``prepared`` is safe to preserve/reuse because the external boundary was not yet
        crossed; ``reconciled_no_effect`` is also safe because evidence established that
        no remote mutation occurred.
        """
        prior = {
            str(row.get("effect_id", "")): row
            for row in accepted_before.get("external_effect_intents", [])
            if row.get("effect_id")
        }
        timestamp = datetime.now(timezone.utc).isoformat()
        for row in dirty.get("external_effect_intents", []):
            effect_id = str(row.get("effect_id", ""))
            before = prior.get(effect_id)
            changed = before is None or any(
                before.get(field) != row.get(field)
                for field in ("status", "updated_at", "result_sha256", "evidence", "error")
            )
            if changed and str(row.get("status", "")) in cls._CROSSED_EFFECT_STATUSES:
                row["status"] = "indeterminate"
                row["updated_at"] = timestamp
                row["error"] = (
                    "external boundary was crossed inside a rolled-back accepted transition; "
                    "reconcile remote state before any matching retry"
                )

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

            for table in ("owner_control_state", "human_approvals", "external_effect_intents"):
                if cls._table_exists(conn, table):
                    cls._insert_rows(conn, table, state.get(table, []))

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

            if cls._table_exists(conn, "resource_events"):
                cls._insert_rows(conn, "resource_events", state.get("resource_events", []))
            if cls._table_exists(conn, "verification_receipt_consumptions"):
                cls._insert_rows(
                    conn,
                    "verification_receipt_consumptions",
                    state.get("verification_receipt_consumptions", []),
                )
            if cls._table_exists(conn, "ecology_work_items"):
                cls._insert_rows(conn, "ecology_work_items", state.get("ecology_work_items", []))
            if cls._table_exists(conn, "resource_ingress_events"):
                cls._insert_rows(
                    conn,
                    "resource_ingress_events",
                    state.get("resource_ingress_events", []),
                )

            check = conn.execute("PRAGMA integrity_check").fetchone()
            if check is None or str(check[0]).lower() != "ok":
                raise RuntimeError("external safety-state reapply failed SQLite integrity_check")

    def _atomic_write_journal(self, payload: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.journal_path.with_suffix(".tmp")
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        with temp.open("w", encoding="utf-8") as handle:
            os.chmod(temp, 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.journal_path)
        fsync_directory(self.root)

    def _atomic_write_safety(self, state: dict[str, list[dict[str, Any]]]) -> None:
        payload = {
            "schema_version": self.JOURNAL_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "state": state,
        }
        temp = self.safety_path.with_name(
            f".{self.safety_path.name}.{uuid4().hex}.tmp"
        )
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        with temp.open("w", encoding="utf-8") as handle:
            os.chmod(temp, 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.safety_path)
        fsync_directory(self.root)

    def _read_safety(self) -> dict[str, list[dict[str, Any]]] | None:
        if not self.safety_path.is_file():
            return None
        payload = json.loads(self.safety_path.read_text(encoding="utf-8"))
        if int(payload.get("schema_version", 0)) != self.JOURNAL_VERSION:
            raise RuntimeError("unsupported rollback safety journal schema")
        state = payload.get("state")
        if not isinstance(state, dict):
            raise RuntimeError("rollback safety journal has no state object")
        return {
            str(table): list(rows) if isinstance(rows, list) else []
            for table, rows in state.items()
        }

    @staticmethod
    def _durable_unlink(path: Path) -> None:
        existed = path.exists() or path.is_symlink()
        path.unlink(missing_ok=True)
        if existed:
            fsync_directory(path.parent)

    def _journal_payload(self) -> dict[str, Any]:
        if self._checkpoint is None:
            raise RuntimeError("transition has no Chronicle checkpoint")
        return {
            "schema_version": self.JOURNAL_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database": str(self.database),
            "backup": str(self.backup_path),
            "workspace_backup": str(self.workspace_backup_path),
            "workspace_existed": self._workspace_existed,
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
            self.safety_path.unlink(missing_ok=True)
            self._sqlite_backup(self.database, self.backup_path)
            self._checkpoint = self.chronicle.checkpoint()
            if self.chronicle.path.is_file():
                with self.chronicle.path.open("rb") as handle:
                    os.fsync(handle.fileno())
            fsync_directory(self.chronicle.path.parent)
            self._snapshot_workspace()
            self._atomic_write_journal(self._journal_payload())
            self._entered = True
            return self
        except Exception:
            if not self.journal_path.exists():
                self.backup_path.unlink(missing_ok=True)
                self._remove_tree(self.workspace_backup_path)
                self.safety_path.unlink(missing_ok=True)
                if self.root.exists():
                    fsync_directory(self.root)
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
        if self._checkpoint is None:
            raise RuntimeError(
                "cannot accept transition: Chronicle checkpoint invariant is missing"
            )
        anchor_ok, anchor_error = self.chronicle.contains_anchor(
            self._checkpoint.seq, self._checkpoint.hash
        )
        if not anchor_ok:
            raise RuntimeError(
                "cannot accept transition: prior Chronicle head is no longer an ancestor: "
                + str(anchor_error)
                )
        self._accepted = True
        self._durable_unlink(self.journal_path)
        self.backup_path.unlink(missing_ok=True)
        self._remove_tree(self.workspace_backup_path)
        self.safety_path.unlink(missing_ok=True)
        fsync_directory(self.root)

    def rollback(self, reason: str) -> TransitionRecovery:
        if not self._entered:
            raise RuntimeError("transition guard is not active")
        return self._rollback_locked(reason)

    def _rollback_locked(self, reason: str) -> TransitionRecovery:
        if self._checkpoint is None:
            raise RuntimeError("transition rollback has no Chronicle checkpoint")
        safety = self._read_safety()
        if safety is None:
            safety = self._export_external_safety_state(self.database)
            accepted_before = self._export_external_safety_state(self.backup_path)
            self._quarantine_rolled_back_effects(safety, accepted_before)
            self._atomic_write_safety(safety)
        self._sqlite_restore(self.backup_path, self.database)
        self.chronicle.restore_checkpoint(self._checkpoint)
        self._restore_workspace()
        self._restore_external_safety_state(self.database, safety)
        self._durable_unlink(self.journal_path)
        self.backup_path.unlink(missing_ok=True)
        self._remove_tree(self.workspace_backup_path)
        self.safety_path.unlink(missing_ok=True)
        fsync_directory(self.root)
        return TransitionRecovery(
            recovered=True,
            reason=str(reason)[:4000],
            chronicle_seq=self._checkpoint.seq,
            preserved_external_intents=len(safety.get("work_port_intents", [])),
            preserved_external_submissions=len(safety.get("work_port_submissions", [])),
            preserved_external_observations=len(safety.get("observations", [])),
            preserved_external_effects=len(safety.get("external_effect_intents", [])),
            preserved_owner_controls=(
                len(safety.get("owner_control_state", []))
                + len(safety.get("human_approvals", []))
            ),
            preserved_resource_ingress=len(safety.get("resource_ingress_events", [])),
        )

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
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
    def recover_incomplete(
        cls,
        state_dir: Path,
        chronicle: Chronicle,
        *,
        lock_held: bool = False,
    ) -> TransitionRecovery:
        """Recover a process-killed transition before normal boot mutates state."""
        guard = cls(state_dir, chronicle)

        def recover_locked() -> TransitionRecovery:
            if not guard.journal_path.is_file():
                guard.backup_path.unlink(missing_ok=True)
                guard._remove_tree(guard.workspace_backup_path)
                guard.safety_path.unlink(missing_ok=True)
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
            guard._workspace_existed = bool(payload.get("workspace_existed", False))
            return guard._rollback_locked("recovered interrupted production transition before boot")

        if lock_held:
            return recover_locked()
        with guard._exclusive_lock():
            return recover_locked()
