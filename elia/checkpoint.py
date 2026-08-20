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
import tempfile
from typing import Any
from uuid import uuid4
import zipfile

from nacl.exceptions import CryptoError
from nacl.secret import Aead

from .chronicle import Chronicle
from .memory import MemoryStore


CHECKPOINT_VERSION = 1
MANIFEST_NAME = "manifest.json"
SIGNATURE_NAME = "manifest.hmac"
STATE_PREFIX = "state/"
ANCHOR_NAME = "checkpoint.anchor.json"

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
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


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
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


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

    def _memory(self) -> MemoryStore:
        return MemoryStore(self.state_dir / "memory.sqlite3")

    def _read_anchor(self) -> dict[str, Any] | None:
        if not self.anchor_path.exists():
            return None
        try:
            return json.loads(self.anchor_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointError(f"invalid local checkpoint anchor: {exc}") from exc

    def _backup_sqlite(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_conn = sqlite3.connect(source, timeout=30.0)
        dest_conn = sqlite3.connect(destination)
        try:
            source_conn.execute("PRAGMA busy_timeout=30000")
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
            source_conn.close()

    def _workspace_fingerprint(self) -> str:
        workspace = self.state_dir / "workspace"
        digest = sha256()
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
        if not workspace.exists():
            return
        for source in sorted(workspace.rglob("*")):
            relative = source.relative_to(workspace)
            if source.is_symlink():
                raise CheckpointError(f"workspace symlink is not checkpointable: {relative}")
            target = destination / "workspace" / relative
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

    def _manifest_files(self, staged_state: Path) -> dict[str, dict[str, Any]]:
        files: dict[str, dict[str, Any]] = {}
        total = 0
        for path in sorted(staged_state.rglob("*")):
            if not path.is_file():
                continue
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
            }
        if len(files) > MAX_CHECKPOINT_MEMBERS - 2:
            raise CheckpointError("checkpoint has too many state files")
        return files

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
            handle.write(ENVELOPE_MAGIC)
            handle.write(encrypted)
            handle.flush()
            os.fsync(handle.fileno())

    def export(self, destination: Path) -> CheckpointInfo:
        destination = Path(destination)
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
                    "files": self._manifest_files(staged_state),
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
                os.replace(temp_output, destination)
            else:
                os.replace(temp_archive, destination)

            memory.set_meta("checkpoint_counter", str(counter))
            memory.set_meta("checkpoint_digest", checkpoint_digest)
            _atomic_json(
                self.anchor_path,
                {"counter": counter, "digest": checkpoint_digest, "created_at": created_at},
            )
            return CheckpointInfo(
                path=destination,
                digest=checkpoint_digest,
                counter=counter,
                created_at=created_at,
                chronicle_seq=before_seq,
                chronicle_hash=before_hash,
            )
        finally:
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
                payload = archive.read(name)
                if len(payload) != int(metadata["size"]):
                    raise CheckpointError(f"checkpoint size mismatch: {name}")
                if sha256(payload).hexdigest() != metadata["sha256"]:
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

    def _enforce_local_rollback_policy(self, counter: int, digest: str) -> None:
        anchor = self._read_anchor()
        if not anchor:
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

    def restore(
        self, checkpoint: Path, expected_digest: str | None = None
    ) -> CheckpointInfo:
        checkpoint = Path(checkpoint)
        archive, manifest, digest = self._read_verified_archive(
            checkpoint, expected_digest
        )
        counter = int(manifest["checkpoint_counter"])
        self._enforce_local_rollback_policy(counter, digest)

        parent = self.state_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        staging = parent / f".{self.state_dir.name}.restore-{uuid4().hex}"
        backup = parent / f".{self.state_dir.name}.backup-{uuid4().hex}"
        swapped = False

        try:
            staging.mkdir(parents=True)
            files = manifest["files"]
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

            _atomic_json(
                staging / ANCHOR_NAME,
                {
                    "counter": counter,
                    "digest": digest,
                    "created_at": manifest["created_at"],
                },
            )

            if self.state_dir.exists():
                os.replace(self.state_dir, backup)
            os.replace(staging, self.state_dir)
            swapped = True

            restored_memory = MemoryStore(self.state_dir / "memory.sqlite3")
            restored_memory.set_meta("checkpoint_counter", str(counter))
            restored_memory.set_meta("checkpoint_digest", digest)
            restored_memory.set_meta("restored_from_checkpoint", digest)

            if backup.exists():
                shutil.rmtree(backup)

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
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if swapped and self.state_dir.exists():
                shutil.rmtree(self.state_dir, ignore_errors=True)
            if backup.exists() and not self.state_dir.exists():
                os.replace(backup, self.state_dir)
            raise
        finally:
            archive.close()
