from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import hmac
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
from typing import Any
from uuid import uuid4
import zipfile

from nacl.exceptions import CryptoError
from nacl.secret import Aead

from .canonical import canonical_json_bytes
from .chronicle import Chronicle
from .memory import MemoryStore
from .transition_kernel import (
    AcceptedTransitionGuard,
    StateWriterLock,
    fsync_directory,
    state_writer_lock_path,
)


CHECKPOINT_VERSION = 1
MANIFEST_NAME = "manifest.json"
SIGNATURE_NAME = "manifest.hmac"
STATE_PREFIX = "state/"
ANCHOR_NAME = "checkpoint.anchor.json"
PUBLISH_JOURNAL_VERSION = 1

ENCRYPTION_KEY_ENV = "ELIA_CHECKPOINT_ENCRYPTION_KEY"
REQUIRE_ENCRYPTION_ENV = "ELIA_CHECKPOINT_REQUIRE_ENCRYPTION"
ENVELOPE_MAGIC = b"ELIA-WILD-CHECKPOINT-ENC-v1\n"
ENVELOPE_AAD = b"ELIA-WILD checkpoint envelope v1"

MAX_CHECKPOINT_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_CHECKPOINT_FILE_BYTES = MAX_CHECKPOINT_ARCHIVE_BYTES + 4096
MAX_CHECKPOINT_MEMBER_BYTES = 256 * 1024 * 1024
MAX_CHECKPOINT_TOTAL_UNCOMPRESSED_BYTES = 768 * 1024 * 1024
MAX_CHECKPOINT_MEMBERS = 4096


class CheckpointError(RuntimeError):
    pass


class CheckpointAuthenticationError(CheckpointError):
    pass


class CheckpointEncryptionError(CheckpointAuthenticationError):
    pass


class CheckpointRollbackError(CheckpointError):
    pass


@dataclass(frozen=True, slots=True)
class CheckpointInfo:
    path: Path
    digest: str
    counter: int
    created_at: str
    chronicle_seq: int
    chronicle_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "digest": self.digest,
            "counter": self.counter,
            "created_at": self.created_at,
            "chronicle_seq": self.chronicle_seq,
            "chronicle_hash": self.chronicle_hash,
        }


def _canonical_json(value: Any) -> bytes:
    return canonical_json_bytes(value)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_dump_digest(path: Path) -> str:
    """Digest logical SQLite contents, independent of WAL/page layout."""
    digest = sha256()
    with sqlite3.connect(path) as conn:
        for statement in conn.iterdump():
            digest.update(statement.encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        os.chmod(temp, 0o600)
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    fsync_directory(path.parent)


def _checkpoint_control_root(state_dir: Path) -> Path:
    state = Path(state_dir).resolve()
    return state.parent / f".{state.name}.checkpoint-control"


def _restore_journal_path(state_dir: Path) -> Path:
    return _checkpoint_control_root(state_dir) / "restore.json"


def _fsync_tree(root: Path) -> None:
    for item in sorted(Path(root).rglob("*")):
        if item.is_file():
            with item.open("rb") as handle:
                os.fsync(handle.fileno())
    directories = [root, *(item for item in Path(root).rglob("*") if item.is_dir())]
    for directory in sorted(
        directories, key=lambda item: len(Path(item).parts), reverse=True
    ):
        fsync_directory(Path(directory))


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _validated_restore_paths(
    state_dir: Path, payload: dict[str, Any]
) -> tuple[Path, Path, Path]:
    state = Path(state_dir).resolve()
    parent = state.parent
    target = Path(str(payload.get("state_dir", ""))).resolve()
    staging = Path(str(payload.get("staging", ""))).resolve()
    backup = Path(str(payload.get("backup", ""))).resolve()
    if target != state:
        raise CheckpointError("restore journal targets a different state directory")
    if staging.parent != parent or not staging.name.startswith(f".{state.name}.restore-"):
        raise CheckpointError("restore journal has an unsafe staging path")
    if backup.parent != parent or not backup.name.startswith(f".{state.name}.backup-"):
        raise CheckpointError("restore journal has an unsafe backup path")
    if staging.is_symlink() or backup.is_symlink():
        raise CheckpointError("restore journal references a symlinked recovery path")
    return target, staging, backup


def recover_interrupted_restore(
    state_dir: Path, *, lock_held: bool = False
) -> bool:
    """Fail safely after process death between the two restore directory renames.

    Before the durable ``new_moved`` marker, recovery prefers the old accepted state.
    Once that marker is durable, recovery keeps the already verified replacement and
    merely finishes cleanup. The journal and all referenced paths are outside/alongside
    ``state_dir``, so replacing the directory cannot erase the recovery evidence.
    """

    state = Path(state_dir).resolve()

    def recover_locked() -> bool:
        journal = _restore_journal_path(state)
        if not journal.is_file():
            return False
        try:
            payload = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointError(f"invalid checkpoint restore journal: {exc}") from exc
        if int(payload.get("schema_version", 0)) != PUBLISH_JOURNAL_VERSION:
            raise CheckpointError("unsupported checkpoint restore journal schema")
        target, staging, backup = _validated_restore_paths(state, payload)
        status = str(payload.get("status", ""))
        if status not in {"prepared", "old_moved", "new_moved"}:
            raise CheckpointError("checkpoint restore journal has an invalid phase")
        if not isinstance(payload.get("had_original"), bool):
            raise CheckpointError("checkpoint restore journal has invalid origin state")
        had_original = bool(payload["had_original"])

        if status == "new_moved":
            if target.is_symlink() or not target.is_dir():
                raise CheckpointError(
                    "restore journal says replacement was published but state is missing"
                )
            _remove_path(staging)
            _remove_path(backup)
        elif had_original:
            if not backup.is_dir():
                if status == "prepared" and target.is_dir():
                    _remove_path(staging)
                else:
                    raise CheckpointError(
                        "interrupted restore cannot recover the prior accepted state"
                    )
            else:
                displaced = target.parent / f".{target.name}.interrupted-{uuid4().hex}"
                if target.exists() or target.is_symlink():
                    os.replace(target, displaced)
                    fsync_directory(target.parent)
                os.replace(backup, target)
                fsync_directory(target.parent)
                _remove_path(displaced)
                _remove_path(staging)
        else:
            if status == "old_moved" and staging.is_dir() and not target.exists():
                os.replace(staging, target)
                fsync_directory(target.parent)
            else:
                _remove_path(staging)
            _remove_path(backup)

        journal.unlink(missing_ok=True)
        fsync_directory(journal.parent)
        return True

    if lock_held:
        return recover_locked()
    with StateWriterLock(state):
        return recover_locked()


def _safe_member(name: str) -> PurePosixPath:
    item = PurePosixPath(name)
    if item.is_absolute() or ".." in item.parts or not item.parts:
        raise CheckpointError(f"unsafe checkpoint member: {name!r}")
    return item


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _decode_encryption_key(raw: str) -> bytes:
    value = str(raw).strip()
    if value.startswith("base64:"):
        value = value.split(":", 1)[1].strip()
    try:
        key = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise CheckpointEncryptionError(
            f"{ENCRYPTION_KEY_ENV} must contain a base64-encoded 32-byte key"
        ) from exc
    if len(key) != Aead.KEY_SIZE:
        raise CheckpointEncryptionError(
            f"{ENCRYPTION_KEY_ENV} must decode to exactly {Aead.KEY_SIZE} bytes"
        )
    return key


def _environment_encryption_key() -> bytes | None:
    raw = os.getenv(ENCRYPTION_KEY_ENV, "").strip()
    if not raw:
        return None
    return _decode_encryption_key(raw)


class CheckpointManager:
    """Authenticated, optionally encrypted, versioned ELIA state checkpoint manager.

    HMAC and manifest hashes preserve state integrity and rollback anchors. When an
    encryption key is configured, the complete authenticated ZIP is additionally sealed
    with XChaCha20-Poly1305 (PyNaCl ``Aead``), hiding memory/Chronicle/workspace contents
    at rest. External persistence should set ``ELIA_CHECKPOINT_REQUIRE_ENCRYPTION=1`` so
    plaintext legacy archives fail closed rather than silently leaving private state
    readable. Encryption keys are accepted only as explicit 32-byte keys; this module
    never derives encryption strength from a short password.
    """

    def __init__(
        self,
        state_dir: Path,
        identity_name: str,
        key: bytes,
        identity_fingerprint: str | None = None,
        *,
        encryption_key: bytes | None = None,
        require_encryption: bool | None = None,
    ):
        self.state_dir = Path(state_dir).resolve()
        self.identity_name = identity_name
        self.identity_fingerprint = (
            str(identity_fingerprint).strip() if identity_fingerprint else None
        )
        if len(key) < 16:
            raise ValueError("checkpoint authentication key must be at least 16 bytes")
        self.key = key

        resolved_encryption_key = encryption_key
        if resolved_encryption_key is None:
            resolved_encryption_key = _environment_encryption_key()
        if resolved_encryption_key is not None and len(resolved_encryption_key) != Aead.KEY_SIZE:
            raise ValueError(
                f"checkpoint encryption key must be exactly {Aead.KEY_SIZE} bytes"
            )
        self.encryption_key = resolved_encryption_key
        self.require_encryption = (
            _truthy_env(REQUIRE_ENCRYPTION_ENV)
            if require_encryption is None
            else bool(require_encryption)
        )

    @property
    def anchor_path(self) -> Path:
        return self.state_dir / ANCHOR_NAME

    @property
    def control_root(self) -> Path:
        return _checkpoint_control_root(self.state_dir)

    @property
    def publish_journal_path(self) -> Path:
        return self.control_root / "publish.json"

    def _memory(self) -> MemoryStore:
        return MemoryStore(self.state_dir / "memory.sqlite3")

    def _read_anchor(self) -> dict[str, Any] | None:
        if not self.anchor_path.exists():
            return None
        try:
            anchor = json.loads(self.anchor_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointError(f"invalid local checkpoint anchor: {exc}") from exc
        if not isinstance(anchor, dict):
            raise CheckpointError("invalid local checkpoint anchor: expected JSON object")
        if int(anchor.get("schema_version", 0) or 0) != 2:
            raise CheckpointAuthenticationError(
                "unauthenticated legacy checkpoint anchor is not a trusted rollback witness; "
                "restore/bootstrap with an independently verified expected digest"
            )
        signature = str(anchor.get("hmac_sha256") or "")
        payload = {key: value for key, value in anchor.items() if key != "hmac_sha256"}
        expected = hmac.new(self.key, _canonical_json(payload), sha256).hexdigest()
        if not signature or not hmac.compare_digest(signature, expected):
            raise CheckpointAuthenticationError(
                "local checkpoint anchor authentication failed"
            )
        try:
            counter = int(anchor["counter"])
            digest = str(anchor["digest"]).lower()
            previous = str(anchor.get("previous_digest") or "").lower()
            created_at = str(anchor["created_at"])
            digest_valid = len(digest) == 64 and int(digest, 16) >= 0
            previous_valid = not previous or (
                len(previous) == 64 and int(previous, 16) >= 0
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CheckpointError(f"invalid local checkpoint anchor fields: {exc}") from exc
        if (
            counter < 1
            or not digest_valid
            or not previous_valid
            or (counter == 1 and bool(previous))
            or (counter > 1 and not previous)
            or not created_at
        ):
            raise CheckpointError("invalid local checkpoint anchor lineage fields")
        return anchor

    def _write_anchor(
        self,
        *,
        counter: int,
        digest: str,
        created_at: str,
        previous_digest: str,
        path: Path | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "schema_version": 2,
            "counter": int(counter),
            "digest": str(digest),
            "previous_digest": str(previous_digest),
            "created_at": str(created_at),
        }
        payload["hmac_sha256"] = hmac.new(
            self.key, _canonical_json(payload), sha256
        ).hexdigest()
        _atomic_json(path or self.anchor_path, payload)

    def _commit_memory_metadata(
        self,
        database: Path,
        *,
        counter: int,
        digest: str,
        restored_from: str | None = None,
    ) -> None:
        with sqlite3.connect(database, timeout=30.0) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            values = {
                "checkpoint_counter": str(int(counter)),
                "checkpoint_digest": str(digest),
            }
            if restored_from is not None:
                values["restored_from_checkpoint"] = str(restored_from)
            for key, value in values.items():
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(FULL)")
        for candidate in (
            database,
            database.with_name(database.name + "-wal"),
            database.with_name(database.name + "-shm"),
        ):
            if candidate.is_file():
                with candidate.open("rb") as handle:
                    os.fsync(handle.fileno())
        fsync_directory(database.parent)

    def _publish_envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        envelope = {
            "publish": payload,
            "hmac_sha256": hmac.new(
                self.key, _canonical_json(payload), sha256
            ).hexdigest(),
        }
        return envelope

    def _read_publish_journal(self) -> dict[str, Any] | None:
        path = self.publish_journal_path
        if not path.is_file():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointError(f"invalid checkpoint publish journal: {exc}") from exc
        if not isinstance(envelope, dict) or not isinstance(envelope.get("publish"), dict):
            raise CheckpointError("checkpoint publish journal has no payload")
        payload = dict(envelope["publish"])
        expected = hmac.new(self.key, _canonical_json(payload), sha256).hexdigest()
        if not hmac.compare_digest(str(envelope.get("hmac_sha256") or ""), expected):
            raise CheckpointAuthenticationError(
                "checkpoint publish journal authentication failed"
            )
        if int(payload.get("schema_version", 0)) != PUBLISH_JOURNAL_VERSION:
            raise CheckpointError("unsupported checkpoint publish journal schema")
        if Path(str(payload.get("state_dir", ""))).resolve() != self.state_dir:
            raise CheckpointError("checkpoint publish journal belongs to another state")
        return payload

    def _backup_sqlite(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_conn = sqlite3.connect(source, timeout=30.0)
        dest_conn = sqlite3.connect(destination)
        try:
            source_conn.execute("PRAGMA busy_timeout=30000")
            source_conn.backup(dest_conn)
            # A checkpoint carries one self-contained database file. Normalize the
            # temporary backup out of WAL mode before hashing/enumerating it; otherwise
            # transient -wal/-shm files can disappear between manifest creation and ZIP
            # writing, producing a nondeterministically incomplete archive.
            dest_conn.commit()
            dest_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            dest_conn.execute("PRAGMA journal_mode=DELETE")
            dest_conn.commit()
        finally:
            dest_conn.close()
            source_conn.close()
        for suffix in ("-wal", "-shm"):
            destination.with_name(destination.name + suffix).unlink(missing_ok=True)
        with destination.open("rb") as handle:
            os.fsync(handle.fileno())
        fsync_directory(destination.parent)

    def _checkpoint_matches(self, path: Path, digest: str) -> bool:
        if not path.is_file():
            return False
        try:
            self.inspect(path, expected_digest=digest)
        except CheckpointError:
            return False
        return True

    def _write_publish_journal(self, payload: dict[str, Any]) -> None:
        existed = self.control_root.exists()
        self.control_root.mkdir(parents=True, exist_ok=True)
        if not existed:
            fsync_directory(self.control_root.parent)
        _atomic_json(self.publish_journal_path, self._publish_envelope(payload))

    def _write_restore_journal(self, payload: dict[str, Any]) -> None:
        existed = self.control_root.exists()
        self.control_root.mkdir(parents=True, exist_ok=True)
        if not existed:
            fsync_directory(self.control_root.parent)
        _atomic_json(_restore_journal_path(self.state_dir), payload)

    def _finish_pending_publish(self) -> CheckpointInfo | None:
        payload = self._read_publish_journal()
        if payload is None:
            return None
        destination = Path(str(payload["destination"])).resolve()
        candidate = Path(str(payload["candidate"])).resolve()
        digest = str(payload["digest"])
        counter = int(payload["counter"])
        created_at = str(payload["created_at"])
        previous_digest = str(payload.get("previous_digest") or "")

        if not self._checkpoint_matches(destination, digest):
            if not self._checkpoint_matches(candidate, digest):
                raise CheckpointError(
                    "pending checkpoint publication has neither a trusted destination "
                    "nor a trusted temporary artifact"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(candidate, destination)
            fsync_directory(destination.parent)

        memory_path = self.state_dir / "memory.sqlite3"
        if not memory_path.is_file():
            raise CheckpointError(
                "cannot finish checkpoint publication because memory.sqlite3 is missing"
            )
        anchor = self._read_anchor()
        if anchor:
            anchor_counter = int(anchor.get("counter", 0))
            anchor_digest = str(anchor.get("digest") or "")
            anchor_is_target = anchor_counter == counter and hmac.compare_digest(
                anchor_digest, digest
            )
            anchor_is_predecessor = (
                anchor_counter == counter - 1
                and bool(anchor_digest)
                and hmac.compare_digest(anchor_digest, previous_digest)
            )
            if not (anchor_is_target or anchor_is_predecessor):
                raise CheckpointRollbackError(
                    "pending checkpoint publication conflicts with the authenticated local lineage"
                )
        elif counter != 1 or previous_digest:
            raise CheckpointRollbackError(
                "pending checkpoint publication has no authenticated predecessor anchor"
            )

        memory = self._memory()
        memory_counter = int(memory.get_meta("checkpoint_counter", "0") or "0")
        memory_digest = memory.get_meta("checkpoint_digest", "") or ""
        metadata_is_target = memory_counter == counter and hmac.compare_digest(
            memory_digest, digest
        )
        metadata_is_predecessor = memory_counter == counter - 1 and (
            (counter == 1 and not memory_digest)
            or hmac.compare_digest(memory_digest, previous_digest)
        )
        if not (metadata_is_target or metadata_is_predecessor):
            raise CheckpointRollbackError(
                "pending checkpoint publication conflicts with durable checkpoint metadata"
            )

        self._commit_memory_metadata(
            memory_path, counter=counter, digest=digest
        )
        self._write_anchor(
            counter=counter,
            digest=digest,
            created_at=created_at,
            previous_digest=previous_digest,
        )
        self.publish_journal_path.unlink(missing_ok=True)
        fsync_directory(self.control_root)
        if candidate != destination:
            candidate.unlink(missing_ok=True)
        return CheckpointInfo(
            path=destination,
            digest=digest,
            counter=counter,
            created_at=created_at,
            chronicle_seq=int(payload["chronicle_seq"]),
            chronicle_hash=str(payload["chronicle_hash"]),
        )

    def _workspace_fingerprint(self) -> str:
        workspace = self.state_dir / "workspace"
        digest = sha256()
        if workspace.is_symlink():
            raise CheckpointError("workspace root symlink is not checkpointable")
        if not workspace.exists():
            return digest.hexdigest()
        for source in sorted(workspace.rglob("*")):
            relative = source.relative_to(workspace).as_posix()
            if source.is_symlink():
                raise CheckpointError(f"workspace symlink is not checkpointable: {relative}")
            kind = "D" if source.is_dir() else "F" if source.is_file() else "O"
            digest.update(f"{kind}\0{relative}\0".encode("utf-8"))
            if source.is_file():
                digest.update(str(source.stat().st_size).encode("ascii"))
                digest.update(b"\0")
                digest.update(_sha256_file(source).encode("ascii"))
                digest.update(b"\n")
        return digest.hexdigest()

    def _copy_workspace(self, destination: Path) -> None:
        workspace = self.state_dir / "workspace"
        if workspace.is_symlink():
            raise CheckpointError("workspace root symlink is not checkpointable")
        if not workspace.exists():
            return
        try:
            AcceptedTransitionGuard._copy_tree_durable(
                workspace, destination / "workspace"
            )
        except RuntimeError as exc:
            raise CheckpointError(f"workspace is not safely checkpointable: {exc}") from exc

    def _manifest_files(self, staged_state: Path) -> dict[str, dict[str, Any]]:
        files: dict[str, dict[str, Any]] = {}
        total = 0
        for path in sorted(staged_state.rglob("*")):
            if not path.is_file():
                continue
            if path.name in {"memory.sqlite3-wal", "memory.sqlite3-shm"}:
                raise CheckpointError(
                    "staged checkpoint database still has transient SQLite sidecars"
                )
            size = path.stat().st_size
            if size > MAX_CHECKPOINT_MEMBER_BYTES:
                raise CheckpointError(
                    f"checkpoint member exceeds size limit: {path.name} ({size} bytes)"
                )
            total += size
            if total > MAX_CHECKPOINT_TOTAL_UNCOMPRESSED_BYTES:
                raise CheckpointError("checkpoint state exceeds total uncompressed size limit")
            relative = path.relative_to(staged_state).as_posix()
            files[f"{STATE_PREFIX}{relative}"] = {
                "sha256": _sha256_file(path),
                "size": size,
                "mode": stat.S_IMODE(path.stat().st_mode),
            }
        if len(files) > MAX_CHECKPOINT_MEMBERS - 2:
            raise CheckpointError("checkpoint has too many state files")
        return files

    def _manifest_directories(self, staged_state: Path) -> dict[str, dict[str, int]]:
        directories: dict[str, dict[str, int]] = {}
        for path in sorted(staged_state.rglob("*")):
            if path.is_dir():
                relative = path.relative_to(staged_state).as_posix()
                directories[relative] = {"mode": stat.S_IMODE(path.stat().st_mode)}
        return directories

    def _validate_export_destination(self, destination: Path) -> Path:
        destination = Path(destination).resolve()
        if destination.is_relative_to(self.state_dir):
            raise CheckpointError(
                "checkpoint destination must be outside the replaceable organism state directory"
            )
        internal_sibling_prefix = f".{self.state_dir.name}."
        if (
            destination == state_writer_lock_path(self.state_dir)
            or destination.is_relative_to(self.control_root)
            or (
                destination.parent == self.state_dir.parent
                and destination.name.startswith(internal_sibling_prefix)
            )
        ):
            raise CheckpointError(
                "checkpoint destination conflicts with organism kernel-control storage"
            )
        return destination

    def _write_encrypted_envelope(self, archive_path: Path, output_path: Path) -> None:
        if self.encryption_key is None:
            raise CheckpointEncryptionError(
                f"encrypted checkpoint required but {ENCRYPTION_KEY_ENV} is not configured"
            )
        size = archive_path.stat().st_size
        if size > MAX_CHECKPOINT_ARCHIVE_BYTES:
            raise CheckpointError("checkpoint archive exceeds encryption size limit")
        plaintext = archive_path.read_bytes()
        encrypted = bytes(Aead(self.encryption_key).encrypt(plaintext, ENVELOPE_AAD))
        with output_path.open("wb") as handle:
            os.chmod(output_path, 0o600)
            handle.write(ENVELOPE_MAGIC)
            handle.write(encrypted)
            handle.flush()
            os.fsync(handle.fileno())

    def export(self, destination: Path) -> CheckpointInfo:
        with StateWriterLock(self.state_dir):
            recover_interrupted_restore(self.state_dir, lock_held=True)
            recovered = self._finish_pending_publish()
            if recovered is not None and recovered.path.resolve() == Path(
                destination
            ).resolve():
                return CheckpointInfo(
                    path=Path(destination),
                    digest=recovered.digest,
                    counter=recovered.counter,
                    created_at=recovered.created_at,
                    chronicle_seq=recovered.chronicle_seq,
                    chronicle_hash=recovered.chronicle_hash,
                )
            return self._export_locked(destination)

    def _export_locked(self, destination: Path) -> CheckpointInfo:
        destination = self._validate_export_destination(destination)
        memory_path = self.state_dir / "memory.sqlite3"
        if not memory_path.exists():
            raise CheckpointError("cannot checkpoint before memory.sqlite3 exists")

        chronicle = Chronicle(self.state_dir / "chronicle.jsonl")
        valid, error = chronicle.verify()
        if not valid:
            raise CheckpointError(f"Chronicle integrity failure: {error}")
        before_seq, before_hash = chronicle.head()
        workspace_before = self._workspace_fingerprint()

        memory = self._memory()
        previous_counter = int(memory.get_meta("checkpoint_counter", "0") or "0")
        previous_digest = memory.get_meta("checkpoint_digest", "") or ""
        anchor = self._read_anchor()
        if anchor is None:
            if previous_counter != 0 or previous_digest:
                raise CheckpointRollbackError(
                    "checkpoint metadata exists without its authenticated local anchor"
                )
        else:
            anchor_counter = int(anchor.get("counter", 0))
            anchor_digest = str(anchor.get("digest") or "")
            if previous_counter != anchor_counter or not hmac.compare_digest(
                previous_digest, anchor_digest
            ):
                raise CheckpointRollbackError(
                    "checkpoint metadata and authenticated local anchor disagree"
                )
        counter = previous_counter + 1
        persisted_identity_fp = memory.get_meta("identity_bundle_fingerprint")
        identity_meta = {
            "boot_count": memory.get_meta("boot_count", "0"),
            "genesis_initialized": memory.get_meta("genesis_initialized", "0"),
            "identity_bundle_fingerprint": persisted_identity_fp or self.identity_fingerprint,
            "subject_core_fingerprint": memory.get_meta("subject_core_fingerprint"),
            "constitution_fingerprint": memory.get_meta("constitution_fingerprint"),
            "prompt_fingerprint": memory.get_meta("prompt_fingerprint"),
            "self_model_fingerprint": memory.get_meta("self_model_fingerprint"),
            "body_version": memory.get_meta("body_version"),
            "branch_id": memory.get_meta("branch_id"),
        }
        if (
            self.identity_fingerprint
            and persisted_identity_fp
            and persisted_identity_fp != self.identity_fingerprint
        ):
            raise CheckpointError(
                "refusing checkpoint export: durable identity fingerprint differs from loaded body"
            )

        if self.require_encryption and self.encryption_key is None:
            raise CheckpointEncryptionError(
                f"checkpoint encryption is required but {ENCRYPTION_KEY_ENV} is not configured"
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex
        temp_archive = destination.with_name(f".{destination.name}.{token}.archive.tmp")
        temp_output = destination.with_name(f".{destination.name}.{token}.output.tmp")

        try:
            temp_archive.touch(mode=0o600, exist_ok=False)
            with tempfile.TemporaryDirectory(prefix="elia-checkpoint-") as temp_dir_raw:
                temp_dir = Path(temp_dir_raw)
                staged_state = temp_dir / "state"
                staged_state.mkdir(parents=True)
                staged_memory = staged_state / "memory.sqlite3"
                verification_memory = temp_dir / "memory-after.sqlite3"

                self._backup_sqlite(memory_path, staged_memory)
                staged_sqlite_digest = _sqlite_dump_digest(staged_memory)

                chronicle_path = self.state_dir / "chronicle.jsonl"
                if chronicle_path.exists():
                    shutil.copy2(chronicle_path, staged_state / "chronicle.jsonl")
                else:
                    (staged_state / "chronicle.jsonl").write_text("", encoding="utf-8")
                self._copy_workspace(staged_state)

                self._backup_sqlite(memory_path, verification_memory)
                sqlite_after = _sqlite_dump_digest(verification_memory)
                workspace_after = self._workspace_fingerprint()
                valid_after, error_after = chronicle.verify()
                after_seq, after_hash = chronicle.head()
                changed = []
                if not valid_after or (after_seq, after_hash) != (before_seq, before_hash):
                    changed.append("chronicle")
                if sqlite_after != staged_sqlite_digest:
                    changed.append("sqlite")
                if workspace_after != workspace_before:
                    changed.append("workspace")
                if changed:
                    detail = ", ".join(changed)
                    raise CheckpointError(
                        f"state changed while checkpointing ({detail}); stop/quiesce the runtime and retry"
                        + (f": {error_after}" if error_after else "")
                    )

                created_at = datetime.now(timezone.utc).isoformat()
                files = self._manifest_files(staged_state)
                directories = self._manifest_directories(staged_state)
                if len(files) + len(directories) > MAX_CHECKPOINT_MEMBERS - 2:
                    raise CheckpointError("checkpoint has too many state paths")
                manifest = {
                    "version": CHECKPOINT_VERSION,
                    "created_at": created_at,
                    "identity_name": self.identity_name,
                    "checkpoint_counter": counter,
                    "previous_checkpoint_digest": previous_digest,
                    "chronicle": {"seq": before_seq, "hash": before_hash},
                    "identity_meta": identity_meta,
                    "capture_consistency": {
                        "sqlite_logical_sha256": staged_sqlite_digest,
                        "workspace_sha256": workspace_before,
                    },
                    "state_directory_mode": stat.S_IMODE(self.state_dir.stat().st_mode),
                    "directories": directories,
                    "files": files,
                }
                manifest_bytes = _canonical_json(manifest)
                signature = hmac.new(self.key, manifest_bytes, sha256).hexdigest()
                checkpoint_digest = sha256(
                    manifest_bytes + signature.encode("ascii")
                ).hexdigest()

                with zipfile.ZipFile(
                    temp_archive, "w", compression=zipfile.ZIP_DEFLATED
                ) as archive:
                    archive.writestr(MANIFEST_NAME, manifest_bytes)
                    archive.writestr(SIGNATURE_NAME, signature)
                    for path in sorted(staged_state.rglob("*")):
                        if path.is_file():
                            relative = path.relative_to(staged_state).as_posix()
                            archive.write(path, f"{STATE_PREFIX}{relative}")

            if temp_archive.stat().st_size > MAX_CHECKPOINT_ARCHIVE_BYTES:
                raise CheckpointError("checkpoint archive exceeds size limit")

            if self.encryption_key is not None:
                self._write_encrypted_envelope(temp_archive, temp_output)
                candidate = temp_output
            else:
                candidate = temp_archive
                with candidate.open("rb") as handle:
                    os.fsync(handle.fileno())

            publish = {
                "schema_version": PUBLISH_JOURNAL_VERSION,
                "state_dir": str(self.state_dir),
                "destination": str(destination.resolve()),
                "candidate": str(candidate.resolve()),
                "counter": counter,
                "digest": checkpoint_digest,
                "created_at": created_at,
                "previous_digest": previous_digest,
                "chronicle_seq": before_seq,
                "chronicle_hash": before_hash,
            }
            self._write_publish_journal(publish)
            published = self._finish_pending_publish()
            if published is None:
                raise CheckpointError("checkpoint publication journal disappeared")
            return CheckpointInfo(
                path=destination,
                digest=published.digest,
                counter=published.counter,
                created_at=published.created_at,
                chronicle_seq=published.chronicle_seq,
                chronicle_hash=published.chronicle_hash,
            )
        finally:
            pending = self.publish_journal_path.is_file()
            if not pending:
                temp_archive.unlink(missing_ok=True)
                temp_output.unlink(missing_ok=True)

    def _open_checkpoint_archive(self, checkpoint: Path) -> zipfile.ZipFile:
        checkpoint = Path(checkpoint)
        try:
            size = checkpoint.stat().st_size
        except OSError as exc:
            raise CheckpointError(f"cannot read checkpoint: {exc}") from exc
        if size > MAX_CHECKPOINT_FILE_BYTES:
            raise CheckpointError("checkpoint file exceeds size limit")

        try:
            with checkpoint.open("rb") as handle:
                prefix = handle.read(len(ENVELOPE_MAGIC))
                if prefix == ENVELOPE_MAGIC:
                    if self.encryption_key is None:
                        raise CheckpointEncryptionError(
                            f"encrypted checkpoint requires {ENCRYPTION_KEY_ENV}"
                        )
                    encrypted = handle.read()
                    try:
                        plaintext = Aead(self.encryption_key).decrypt(
                            encrypted, ENVELOPE_AAD
                        )
                    except CryptoError as exc:
                        raise CheckpointEncryptionError(
                            "checkpoint encrypted-envelope authentication failed"
                        ) from exc
                    if len(plaintext) > MAX_CHECKPOINT_ARCHIVE_BYTES:
                        raise CheckpointError(
                            "decrypted checkpoint archive exceeds size limit"
                        )
                    return zipfile.ZipFile(BytesIO(plaintext), "r")
        except CheckpointError:
            raise
        except OSError as exc:
            raise CheckpointError(f"cannot read checkpoint: {exc}") from exc

        if self.require_encryption:
            raise CheckpointEncryptionError(
                "plaintext legacy checkpoint rejected because encrypted mode is required"
            )
        try:
            return zipfile.ZipFile(checkpoint, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise CheckpointError(f"invalid checkpoint archive: {exc}") from exc

    def _read_verified_archive(
        self, checkpoint: Path, expected_digest: str | None = None
    ) -> tuple[zipfile.ZipFile, dict[str, Any], str]:
        try:
            archive = self._open_checkpoint_archive(checkpoint)
        except zipfile.BadZipFile as exc:
            raise CheckpointError(f"invalid checkpoint archive: {exc}") from exc

        try:
            infos = archive.infolist()
            if len(infos) > MAX_CHECKPOINT_MEMBERS:
                raise CheckpointError("checkpoint contains too many members")
            raw_names = [info.filename for info in infos]
            if len(raw_names) != len(set(raw_names)):
                raise CheckpointError("checkpoint contains duplicate member names")
            total_uncompressed = 0
            for info in infos:
                _safe_member(info.filename)
                if info.file_size > MAX_CHECKPOINT_MEMBER_BYTES:
                    raise CheckpointError(
                        f"checkpoint member exceeds size limit: {info.filename}"
                    )
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_CHECKPOINT_TOTAL_UNCOMPRESSED_BYTES:
                    raise CheckpointError(
                        "checkpoint exceeds total uncompressed size limit"
                    )

            names = set(raw_names)
            if MANIFEST_NAME not in names or SIGNATURE_NAME not in names:
                raise CheckpointError("checkpoint is missing manifest or signature")

            manifest_bytes = archive.read(MANIFEST_NAME)
            signature = archive.read(SIGNATURE_NAME).decode("ascii").strip()
            expected_signature = hmac.new(self.key, manifest_bytes, sha256).hexdigest()
            if not hmac.compare_digest(signature, expected_signature):
                raise CheckpointAuthenticationError(
                    "checkpoint HMAC authentication failed"
                )

            digest = sha256(manifest_bytes + signature.encode("ascii")).hexdigest()
            if expected_digest and not hmac.compare_digest(
                digest, expected_digest.strip().lower()
            ):
                raise CheckpointRollbackError(
                    "checkpoint does not match the trusted expected digest"
                )

            manifest = json.loads(manifest_bytes)
            if int(manifest.get("version", -1)) != CHECKPOINT_VERSION:
                raise CheckpointError(
                    f"unsupported checkpoint version: {manifest.get('version')}"
                )
            if manifest.get("identity_name") != self.identity_name:
                raise CheckpointError(
                    f"checkpoint identity mismatch: {manifest.get('identity_name')!r} != {self.identity_name!r}"
                )
            counter = int(manifest.get("checkpoint_counter", 0))
            previous = str(manifest.get("previous_checkpoint_digest") or "").lower()
            if counter < 1:
                raise CheckpointError("checkpoint counter must be positive")
            if counter == 1 and previous:
                raise CheckpointError(
                    "first checkpoint must not claim an unverified predecessor"
                )
            if counter > 1:
                try:
                    valid_previous = len(previous) == 64 and int(previous, 16) >= 0
                except ValueError:
                    valid_previous = False
                if not valid_previous:
                    raise CheckpointError(
                        "checkpoint after counter 1 requires a valid predecessor digest"
                    )
            manifest_identity_fp = str(
                (manifest.get("identity_meta") or {}).get(
                    "identity_bundle_fingerprint"
                )
                or ""
            )
            if (
                self.identity_fingerprint
                and manifest_identity_fp
                and manifest_identity_fp != self.identity_fingerprint
            ):
                raise CheckpointError(
                    "checkpoint Subject Core/Constitution fingerprint does not match the loaded identity body"
                )

            files = manifest.get("files")
            if not isinstance(files, dict):
                raise CheckpointError("checkpoint manifest has no file table")
            directories = manifest.get("directories", {})
            if not isinstance(directories, dict):
                raise CheckpointError("checkpoint manifest directory table is invalid")
            if len(files) + len(directories) > MAX_CHECKPOINT_MEMBERS - 2:
                raise CheckpointError("checkpoint manifest has too many state paths")
            state_directory_mode = manifest.get("state_directory_mode")
            if state_directory_mode is not None and (
                not isinstance(state_directory_mode, int)
                or isinstance(state_directory_mode, bool)
                or not 0 <= state_directory_mode <= 0o7777
            ):
                raise CheckpointError("checkpoint state-directory mode is invalid")
            for relative, metadata in directories.items():
                if not isinstance(relative, str) or not relative:
                    raise CheckpointError("checkpoint directory path is invalid")
                item = _safe_member(relative)
                if item.as_posix() != relative:
                    raise CheckpointError(
                        f"checkpoint directory path is not canonical: {relative!r}"
                    )
                if not isinstance(metadata, dict):
                    raise CheckpointError(
                        f"checkpoint directory metadata is invalid: {relative}"
                    )
                mode = metadata.get("mode")
                if (
                    not isinstance(mode, int)
                    or isinstance(mode, bool)
                    or not 0 <= mode <= 0o7777
                ):
                    raise CheckpointError(
                        f"checkpoint directory mode is invalid: {relative}"
                    )
                if f"{STATE_PREFIX}{relative}" in files:
                    raise CheckpointError(
                        f"checkpoint path is both a file and directory: {relative}"
                    )
            expected_names = {MANIFEST_NAME, SIGNATURE_NAME, *files.keys()}
            if names != expected_names:
                unexpected = sorted(names - expected_names)[:8]
                missing = sorted(expected_names - names)[:8]
                raise CheckpointError(
                    f"checkpoint member table mismatch; unexpected={unexpected}, missing={missing}"
                )

            for name, metadata in files.items():
                _safe_member(name)
                if not name.startswith(STATE_PREFIX) or name not in names:
                    raise CheckpointError(f"checkpoint file missing: {name}")
                if not isinstance(metadata, dict):
                    raise CheckpointError(f"checkpoint file metadata is invalid: {name}")
                size = metadata.get("size")
                digest_value = metadata.get("sha256")
                mode = metadata.get("mode")
                if (
                    not isinstance(size, int)
                    or isinstance(size, bool)
                    or size < 0
                    or size > MAX_CHECKPOINT_MEMBER_BYTES
                ):
                    raise CheckpointError(f"checkpoint file size is invalid: {name}")
                if (
                    not isinstance(digest_value, str)
                    or len(digest_value) != 64
                    or any(ch not in "0123456789abcdef" for ch in digest_value)
                ):
                    raise CheckpointError(f"checkpoint file hash is invalid: {name}")
                if mode is not None and (
                    not isinstance(mode, int)
                    or isinstance(mode, bool)
                    or not 0 <= mode <= 0o7777
                ):
                    raise CheckpointError(f"checkpoint file mode is invalid: {name}")
                payload = archive.read(name)
                if len(payload) != size:
                    raise CheckpointError(f"checkpoint size mismatch: {name}")
                if sha256(payload).hexdigest() != digest_value:
                    raise CheckpointError(f"checkpoint hash mismatch: {name}")

            return archive, manifest, digest
        except Exception:
            archive.close()
            raise

    def inspect(
        self, checkpoint: Path, expected_digest: str | None = None
    ) -> CheckpointInfo:
        archive, manifest, digest = self._read_verified_archive(
            Path(checkpoint), expected_digest
        )
        try:
            chronicle = manifest["chronicle"]
            return CheckpointInfo(
                path=Path(checkpoint),
                digest=digest,
                counter=int(manifest["checkpoint_counter"]),
                created_at=str(manifest["created_at"]),
                chronicle_seq=int(chronicle["seq"]),
                chronicle_hash=str(chronicle["hash"]),
            )
        finally:
            archive.close()

    def _enforce_local_rollback_policy(
        self,
        counter: int,
        digest: str,
        manifest: dict[str, Any],
        *,
        expected_digest: str | None,
    ) -> None:
        anchor = self._read_anchor()
        if not anchor:
            if not expected_digest:
                raise CheckpointRollbackError(
                    "fresh-machine restore requires a trusted expected checkpoint digest"
                )
            return
        anchor_counter = int(anchor.get("counter", 0))
        anchor_digest = str(anchor.get("digest", ""))
        if counter < anchor_counter:
            raise CheckpointRollbackError(
                f"rollback detected: checkpoint counter {counter} < trusted local counter {anchor_counter}"
            )
        if counter == anchor_counter and anchor_digest and not hmac.compare_digest(
            digest, anchor_digest
        ):
            raise CheckpointRollbackError(
                "checkpoint fork detected at the trusted local counter"
            )
        if counter > anchor_counter:
            if counter != anchor_counter + 1:
                raise CheckpointRollbackError(
                    "checkpoint counter skips trusted predecessors; restore each authenticated "
                    "successor or bootstrap from an independent fresh-machine trust digest"
                )
            predecessor = str(manifest.get("previous_checkpoint_digest") or "")
            if not anchor_digest or not hmac.compare_digest(predecessor, anchor_digest):
                raise CheckpointRollbackError(
                    "checkpoint predecessor does not match the trusted local anchor"
                )

    def restore(
        self, checkpoint: Path, expected_digest: str | None = None
    ) -> CheckpointInfo:
        with StateWriterLock(self.state_dir):
            recover_interrupted_restore(self.state_dir, lock_held=True)
            self._finish_pending_publish()
            return self._restore_locked(checkpoint, expected_digest=expected_digest)

    def _restore_locked(
        self, checkpoint: Path, expected_digest: str | None = None
    ) -> CheckpointInfo:
        checkpoint = Path(checkpoint)
        archive, manifest, digest = self._read_verified_archive(
            checkpoint, expected_digest
        )
        counter = int(manifest["checkpoint_counter"])
        self._enforce_local_rollback_policy(
            counter,
            digest,
            manifest,
            expected_digest=expected_digest,
        )

        parent = self.state_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        staging = parent / f".{self.state_dir.name}.restore-{uuid4().hex}"
        backup = parent / f".{self.state_dir.name}.backup-{uuid4().hex}"
        restore_journal = _restore_journal_path(self.state_dir)
        journal_written = False

        try:
            staging.mkdir(parents=True)
            files = manifest["files"]
            directories = manifest.get("directories", {})
            for relative in sorted(
                directories,
                key=lambda value: len(PurePosixPath(value).parts),
            ):
                directory = PurePosixPath(relative)
                staging.joinpath(*directory.parts).mkdir(parents=True, exist_ok=True)
            for name in files:
                relative = PurePosixPath(name).relative_to(STATE_PREFIX.rstrip("/"))
                target = staging.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name, "r") as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)

            memory_path = staging / "memory.sqlite3"
            if not memory_path.exists():
                raise CheckpointError("checkpoint has no memory database")
            with sqlite3.connect(memory_path) as conn:
                result = conn.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise CheckpointError(f"SQLite integrity check failed: {result}")

            consistency = manifest.get("capture_consistency") or {}
            expected_sqlite = str(consistency.get("sqlite_logical_sha256") or "")
            if expected_sqlite and _sqlite_dump_digest(memory_path) != expected_sqlite:
                raise CheckpointError(
                    "restored SQLite logical state does not match capture digest"
                )

            restored_chronicle = Chronicle(staging / "chronicle.jsonl")
            valid, error = restored_chronicle.verify()
            if not valid:
                raise CheckpointError(
                    f"restored Chronicle integrity failure: {error}"
                )
            seq, head_hash = restored_chronicle.head()
            expected_head = manifest["chronicle"]
            if seq != int(expected_head["seq"]) or head_hash != str(
                expected_head["hash"]
            ):
                raise CheckpointError(
                    "restored Chronicle head does not match checkpoint manifest"
                )

            # Checkpoint rollback must not resurrect consumed human approvals or erase
            # evidence that an external effect crossed the boundary after this archive
            # was made. Merge only the kernel's narrowly defined external-truth tables;
            # all cognitive/world/workspace state still comes from the checkpoint.
            if (self.state_dir / "memory.sqlite3").is_file():
                safety = AcceptedTransitionGuard._export_external_safety_state(
                    self.state_dir / "memory.sqlite3"
                )
                staged_safety = AcceptedTransitionGuard._export_external_safety_state(
                    memory_path
                )
                AcceptedTransitionGuard._quarantine_rolled_back_effects(
                    safety, staged_safety
                )
                with sqlite3.connect(memory_path) as conn:
                    missing_tables = [
                        table
                        for table, rows in safety.items()
                        if rows
                        and not AcceptedTransitionGuard._table_exists(conn, table)
                    ]
                if missing_tables:
                    raise CheckpointError(
                        "checkpoint schema cannot preserve current external safety state: "
                        + ", ".join(sorted(missing_tables))
                    )
                AcceptedTransitionGuard._restore_external_safety_state(
                    memory_path, safety
                )

            # Commit all state-internal metadata before the directory is published.
            # After the final rename the replacement is already a complete accepted
            # state; no sequence of post-swap SQLite writes can expose a torn head.
            self._commit_memory_metadata(
                memory_path,
                counter=counter,
                digest=digest,
                restored_from=digest,
            )
            self._write_anchor(
                counter=counter,
                digest=digest,
                created_at=str(manifest["created_at"]),
                previous_digest=str(
                    manifest.get("previous_checkpoint_digest") or ""
                ),
                path=staging / ANCHOR_NAME,
            )
            _fsync_tree(staging)
            for name, metadata in files.items():
                mode = metadata.get("mode")
                if mode is None:
                    continue
                relative = PurePosixPath(name).relative_to(STATE_PREFIX.rstrip("/"))
                target = staging.joinpath(*relative.parts)
                descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fchmod(descriptor, int(mode))
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            for relative, metadata in sorted(
                directories.items(),
                key=lambda item: len(PurePosixPath(item[0]).parts),
                reverse=True,
            ):
                target = staging.joinpath(*PurePosixPath(relative).parts)
                descriptor = os.open(
                    target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                )
                try:
                    os.fchmod(descriptor, int(metadata["mode"]))
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            state_directory_mode = manifest.get("state_directory_mode")
            if state_directory_mode is not None:
                descriptor = os.open(
                    staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                )
                try:
                    os.fchmod(descriptor, int(state_directory_mode))
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            fsync_directory(parent)

            payload: dict[str, Any] = {
                "schema_version": PUBLISH_JOURNAL_VERSION,
                "state_dir": str(self.state_dir),
                "staging": str(staging),
                "backup": str(backup),
                "had_original": self.state_dir.exists(),
                "status": "prepared",
                "counter": counter,
                "digest": digest,
            }
            self._write_restore_journal(payload)
            journal_written = True

            if payload["had_original"]:
                os.replace(self.state_dir, backup)
                fsync_directory(parent)
            payload["status"] = "old_moved"
            self._write_restore_journal(payload)

            os.replace(staging, self.state_dir)
            fsync_directory(parent)
            payload["status"] = "new_moved"
            self._write_restore_journal(payload)

            if backup.exists():
                shutil.rmtree(backup)
                fsync_directory(parent)
            restore_journal.unlink(missing_ok=True)
            fsync_directory(self.control_root)
            journal_written = False

            chronicle = manifest["chronicle"]
            return CheckpointInfo(
                path=checkpoint,
                digest=digest,
                counter=counter,
                created_at=str(manifest["created_at"]),
                chronicle_seq=int(chronicle["seq"]),
                chronicle_hash=str(chronicle["hash"]),
            )
        except Exception:
            if journal_written or restore_journal.is_file():
                # Recovery uses the last *durable* phase marker. Before new_moved
                # that intentionally rolls back to the prior accepted state.
                recover_interrupted_restore(self.state_dir, lock_held=True)
            else:
                _remove_path(staging)
                _remove_path(backup)
            raise
        finally:
            archive.close()
