from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
from typing import Any
from uuid import uuid4
import zipfile

from .chronicle import Chronicle
from .memory import MemoryStore


CHECKPOINT_VERSION = 1
MANIFEST_NAME = "manifest.json"
SIGNATURE_NAME = "manifest.hmac"
STATE_PREFIX = "state/"
ANCHOR_NAME = "checkpoint.anchor.json"


class CheckpointError(RuntimeError):
    pass


class CheckpointAuthenticationError(CheckpointError):
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


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def _safe_member(name: str) -> PurePosixPath:
    item = PurePosixPath(name)
    if item.is_absolute() or ".." in item.parts or not item.parts:
        raise CheckpointError(f"unsafe checkpoint member: {name!r}")
    return item


class CheckpointManager:
    """Authenticated, versioned checkpoint export/restore for ELIA state.

    The authentication key is deliberately external to the checkpoint and repository.
    A local anchor detects rollback on an existing machine. On a completely fresh
    machine, strict rollback protection requires an externally trusted expected digest.
    """

    def __init__(self, state_dir: Path, identity_name: str, key: bytes):
        self.state_dir = Path(state_dir)
        self.identity_name = identity_name
        if len(key) < 16:
            raise ValueError("checkpoint authentication key must be at least 16 bytes")
        self.key = key

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
        source_conn = sqlite3.connect(source)
        dest_conn = sqlite3.connect(destination)
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
            source_conn.close()

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
        for path in sorted(staged_state.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(staged_state).as_posix()
            files[f"{STATE_PREFIX}{relative}"] = {
                "sha256": _sha256_file(path),
                "size": path.stat().st_size,
            }
        return files

    def export(self, destination: Path) -> CheckpointInfo:
        destination = Path(destination)
        if not (self.state_dir / "memory.sqlite3").exists():
            raise CheckpointError("cannot checkpoint before memory.sqlite3 exists")

        chronicle = Chronicle(self.state_dir / "chronicle.jsonl")
        valid, error = chronicle.verify()
        if not valid:
            raise CheckpointError(f"Chronicle integrity failure: {error}")
        before_seq, before_hash = chronicle.head()

        memory = self._memory()
        previous_counter = int(memory.get_meta("checkpoint_counter", "0") or "0")
        previous_digest = memory.get_meta("checkpoint_digest", "") or ""
        counter = previous_counter + 1

        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_archive = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")

        try:
            with tempfile.TemporaryDirectory(prefix="elia-checkpoint-") as temp_dir_raw:
                temp_dir = Path(temp_dir_raw)
                staged_state = temp_dir / "state"
                staged_state.mkdir(parents=True)

                self._backup_sqlite(self.state_dir / "memory.sqlite3", staged_state / "memory.sqlite3")
                chronicle_path = self.state_dir / "chronicle.jsonl"
                if chronicle_path.exists():
                    shutil.copy2(chronicle_path, staged_state / "chronicle.jsonl")
                else:
                    (staged_state / "chronicle.jsonl").write_text("", encoding="utf-8")
                self._copy_workspace(staged_state)

                valid_after, error_after = chronicle.verify()
                after_seq, after_hash = chronicle.head()
                if not valid_after or (after_seq, after_hash) != (before_seq, before_hash):
                    raise CheckpointError(
                        "state changed while checkpointing; stop the runtime and retry"
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
                    "identity_meta": {
                        "boot_count": memory.get_meta("boot_count", "0"),
                        "genesis_initialized": memory.get_meta("genesis_initialized", "0"),
                    },
                    "files": self._manifest_files(staged_state),
                }
                manifest_bytes = _canonical_json(manifest)
                signature = hmac.new(self.key, manifest_bytes, sha256).hexdigest()
                checkpoint_digest = sha256(manifest_bytes + signature.encode("ascii")).hexdigest()

                with zipfile.ZipFile(temp_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr(MANIFEST_NAME, manifest_bytes)
                    archive.writestr(SIGNATURE_NAME, signature)
                    for path in sorted(staged_state.rglob("*")):
                        if path.is_file():
                            relative = path.relative_to(staged_state).as_posix()
                            archive.write(path, f"{STATE_PREFIX}{relative}")

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
            if temp_archive.exists():
                temp_archive.unlink(missing_ok=True)

    def _read_verified_archive(
        self, checkpoint: Path, expected_digest: str | None = None
    ) -> tuple[zipfile.ZipFile, dict[str, Any], str]:
        try:
            archive = zipfile.ZipFile(checkpoint, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise CheckpointError(f"invalid checkpoint archive: {exc}") from exc

        try:
            names = set(archive.namelist())
            if MANIFEST_NAME not in names or SIGNATURE_NAME not in names:
                raise CheckpointError("checkpoint is missing manifest or signature")
            for name in names:
                _safe_member(name)

            manifest_bytes = archive.read(MANIFEST_NAME)
            signature = archive.read(SIGNATURE_NAME).decode("ascii").strip()
            expected_signature = hmac.new(self.key, manifest_bytes, sha256).hexdigest()
            if not hmac.compare_digest(signature, expected_signature):
                raise CheckpointAuthenticationError("checkpoint HMAC authentication failed")

            digest = sha256(manifest_bytes + signature.encode("ascii")).hexdigest()
            if expected_digest and not hmac.compare_digest(digest, expected_digest.strip().lower()):
                raise CheckpointRollbackError("checkpoint does not match the trusted expected digest")

            manifest = json.loads(manifest_bytes)
            if int(manifest.get("version", -1)) != CHECKPOINT_VERSION:
                raise CheckpointError(f"unsupported checkpoint version: {manifest.get('version')}")
            if manifest.get("identity_name") != self.identity_name:
                raise CheckpointError(
                    f"checkpoint identity mismatch: {manifest.get('identity_name')!r} != {self.identity_name!r}"
                )

            files = manifest.get("files")
            if not isinstance(files, dict):
                raise CheckpointError("checkpoint manifest has no file table")
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

    def inspect(self, checkpoint: Path, expected_digest: str | None = None) -> CheckpointInfo:
        archive, manifest, digest = self._read_verified_archive(Path(checkpoint), expected_digest)
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
        if counter == anchor_counter and anchor_digest and not hmac.compare_digest(digest, anchor_digest):
            raise CheckpointRollbackError("checkpoint fork detected at the trusted local counter")

    def restore(self, checkpoint: Path, expected_digest: str | None = None) -> CheckpointInfo:
        checkpoint = Path(checkpoint)
        archive, manifest, digest = self._read_verified_archive(checkpoint, expected_digest)
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

            restored_chronicle = Chronicle(staging / "chronicle.jsonl")
            valid, error = restored_chronicle.verify()
            if not valid:
                raise CheckpointError(f"restored Chronicle integrity failure: {error}")
            seq, head_hash = restored_chronicle.head()
            expected_head = manifest["chronicle"]
            if seq != int(expected_head["seq"]) or head_hash != str(expected_head["hash"]):
                raise CheckpointError("restored Chronicle head does not match checkpoint manifest")

            _atomic_json(
                staging / ANCHOR_NAME,
                {"counter": counter, "digest": digest, "created_at": manifest["created_at"]},
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
