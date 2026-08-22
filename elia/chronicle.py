from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module
from pathlib import Path
import json
import os
from types import ModuleType
from typing import Any, Iterator

from .canonical import canonical_json, strict_json_loads
from .redaction import redact_action_record

fcntl: ModuleType | None = None
try:  # Linux is the production/runtime target; keep import optional for tooling portability.
    fcntl = import_module("fcntl")
except ImportError:  # pragma: no cover - non-POSIX fallback has no cross-process guarantee.
    pass


GENESIS_HASH = "0" * 64


@dataclass(slots=True)
class ChronicleEntry:
    seq: int
    timestamp: str
    kind: str
    payload: dict[str, Any]
    previous_hash: str
    hash: str


@dataclass(frozen=True, slots=True)
class ChronicleCheckpoint:
    seq: int
    hash: str
    byte_size: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Chronicle:
    """Append-only JSONL history with SHA-256 chaining and POSIX single-writer locking.

    Cycle records are redacted again at this final persistence boundary so an older or
    alternate runtime implementation cannot accidentally write raw action arguments or
    tool payloads into the durable identity history.

    A valid current chain is not by itself proof of continuity with an older accepted
    chain. `hash_at_seq` / `contains_anchor` expose exact prefix ancestry so CRC/vitals
    can prove that an earlier accepted head is still present in the current history.
    Recoverable file checkpoints allow the accepted-transition kernel to remove only
    unaccepted suffix entries after an interrupted local transition.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    @staticmethod
    def _digest(
        seq: int,
        timestamp: str,
        kind: str,
        payload: dict[str, Any],
        previous_hash: str,
    ) -> str:
        canonical = canonical_json(
            {
                "seq": seq,
                "timestamp": timestamp,
                "kind": kind,
                "payload": payload,
                "previous_hash": previous_hash,
            }
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    @contextmanager
    def _locked(self, *, exclusive: bool) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as handle:
            if fcntl is not None:
                mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(handle.fileno(), mode)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _last_unlocked(self) -> tuple[int, str]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return 0, GENESIS_HASH
        previous_hash = GENESIS_HASH
        expected_seq = 1
        last_seq = 0
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                last_seq, previous_hash = self._validated_item(
                    line,
                    line_number=line_number,
                    expected_seq=expected_seq,
                    previous_hash=previous_hash,
                )
                expected_seq += 1
        return last_seq, previous_hash

    def head(self) -> tuple[int, str]:
        """Return the current sequence/hash head without mutating the Chronicle."""
        with self._locked(exclusive=False):
            try:
                return self._last_unlocked()
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"Chronicle head is unreadable: {exc}") from exc

    def checkpoint(self) -> ChronicleCheckpoint:
        """Capture an exact accepted file boundary under a shared Chronicle lock."""
        with self._locked(exclusive=False):
            try:
                seq, digest = self._last_unlocked()
                size = self.path.stat().st_size if self.path.exists() else 0
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"Chronicle checkpoint is unreadable: {exc}") from exc
        return ChronicleCheckpoint(seq=seq, hash=digest, byte_size=size)

    def append(self, kind: str, payload: dict[str, Any]) -> ChronicleEntry:
        if type(kind) is not str:
            raise TypeError("Chronicle kind must be an exact string")
        if type(payload) is not dict:
            raise TypeError("Chronicle payload must be an exact JSON object")
        persisted_payload = (
            redact_action_record(payload) if kind.upper() == "CYCLE" else payload
        )
        with self._locked(exclusive=True):
            last_seq, previous_hash = self._last_unlocked()
            seq = last_seq + 1
            timestamp = datetime.now(timezone.utc).isoformat()
            digest = self._digest(seq, timestamp, kind, persisted_payload, previous_hash)
            entry = ChronicleEntry(
                seq, timestamp, kind, persisted_payload, previous_hash, digest
            )
            serialized = (
                json.dumps(
                    asdict(entry),
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            )
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            return entry

    @staticmethod
    def _validated_item(
        line: str,
        *,
        line_number: int,
        expected_seq: int,
        previous_hash: str,
    ) -> tuple[int, str]:
        try:
            item = strict_json_loads(line)
            if type(item) is not dict:
                raise TypeError("entry must be an exact JSON object")
            required = {"seq", "timestamp", "kind", "payload", "previous_hash", "hash"}
            if set(item) != required:
                raise ValueError("entry fields differ from the Chronicle schema")
            seq = item["seq"]
            timestamp = item["timestamp"]
            kind = item["kind"]
            payload = item["payload"]
            item_previous = item["previous_hash"]
            item_hash = item["hash"]
            if type(seq) is not int:
                raise TypeError("seq must be an exact integer")
            if type(timestamp) is not str:
                raise TypeError("timestamp must be an exact string")
            if type(kind) is not str:
                raise TypeError("kind must be an exact string")
            if type(payload) is not dict:
                raise TypeError("payload must be an exact JSON object")
            if type(item_previous) is not str:
                raise TypeError("previous_hash must be an exact string")
            if type(item_hash) is not str:
                raise TypeError("hash must be an exact string")
        except (json.JSONDecodeError, KeyError, RecursionError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed entry at line {line_number}: {exc}") from exc
        if seq != expected_seq:
            raise ValueError(f"sequence mismatch at line {line_number}")
        if item_previous != previous_hash:
            raise ValueError(f"previous_hash mismatch at line {line_number}")
        try:
            digest = Chronicle._digest(seq, timestamp, kind, payload, item_previous)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"malformed entry at line {line_number}: {exc}") from exc
        if digest != item_hash:
            raise ValueError(f"hash mismatch at line {line_number}")
        return seq, digest

    def _hash_at_seq_unlocked(self, seq: int) -> str:
        seq = int(seq)
        if seq < 0:
            raise ValueError("Chronicle sequence must be non-negative")
        if seq == 0:
            return GENESIS_HASH
        if not self.path.exists():
            raise LookupError(f"Chronicle has no sequence {seq}")
        previous_hash = GENESIS_HASH
        expected_seq = 1
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                actual_seq, digest = self._validated_item(
                    line,
                    line_number=line_number,
                    expected_seq=expected_seq,
                    previous_hash=previous_hash,
                )
                if actual_seq == seq:
                    return digest
                previous_hash = digest
                expected_seq += 1
        raise LookupError(f"Chronicle has no sequence {seq}")

    def hash_at_seq(self, seq: int) -> str:
        """Return the validated hash at exactly `seq`, or fail closed."""
        with self._locked(exclusive=False):
            try:
                return self._hash_at_seq_unlocked(seq)
            except OSError as exc:
                raise RuntimeError(f"Chronicle read failure: {exc}") from exc

    def contains_anchor(self, seq: int, expected_hash: str) -> tuple[bool, str | None]:
        """Prove that `(seq, hash)` is an exact validated prefix anchor of this chain."""
        expected_hash = str(expected_hash).strip().lower()
        if len(expected_hash) != 64 or any(ch not in "0123456789abcdef" for ch in expected_hash):
            return False, "invalid Chronicle anchor hash"
        try:
            actual = self.hash_at_seq(int(seq))
        except (LookupError, RuntimeError, ValueError) as exc:
            return False, str(exc)
        if actual != expected_hash:
            return False, (
                f"Chronicle ancestry mismatch at seq {int(seq)}: "
                f"current={actual}, expected={expected_hash}"
            )
        return True, None

    def restore_checkpoint(self, checkpoint: ChronicleCheckpoint) -> None:
        """Remove only an unaccepted suffix while preserving the validated prefix.

        This is intentionally not a general history-rewrite API. The requested prefix
        must still be exactly present and the current file may only be longer than the
        captured byte boundary. A missing/tampered prefix fails closed.
        """
        if not isinstance(checkpoint, ChronicleCheckpoint):
            raise TypeError("restore_checkpoint requires ChronicleCheckpoint")
        if checkpoint.byte_size < 0:
            raise ValueError("Chronicle checkpoint byte size must be non-negative")
        with self._locked(exclusive=True):
            try:
                actual = self._hash_at_seq_unlocked(checkpoint.seq)
            except (LookupError, OSError, ValueError) as exc:
                raise RuntimeError(f"cannot restore Chronicle checkpoint: {exc}") from exc
            if actual != checkpoint.hash:
                raise RuntimeError("cannot restore Chronicle checkpoint: accepted prefix hash changed")
            current_size = self.path.stat().st_size if self.path.exists() else 0
            if current_size < checkpoint.byte_size:
                raise RuntimeError("cannot restore Chronicle checkpoint: file moved backward")
            if checkpoint.byte_size == 0:
                if self.path.exists():
                    with self.path.open("r+b") as handle:
                        handle.truncate(0)
                        handle.flush()
                        os.fsync(handle.fileno())
            else:
                with self.path.open("r+b") as handle:
                    handle.truncate(checkpoint.byte_size)
                    handle.flush()
                    os.fsync(handle.fileno())
            restored_seq, restored_hash = self._last_unlocked()
            if restored_seq != checkpoint.seq or restored_hash != checkpoint.hash:
                raise RuntimeError("Chronicle checkpoint restore did not reproduce accepted head")

    def verify(self) -> tuple[bool, str | None]:
        previous_hash = GENESIS_HASH
        expected_seq = 1
        if not self.path.exists():
            return True, None

        with self._locked(exclusive=False):
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if not line.strip():
                            continue
                        try:
                            _, digest = self._validated_item(
                                line,
                                line_number=line_number,
                                expected_seq=expected_seq,
                                previous_hash=previous_hash,
                            )
                        except ValueError as exc:
                            return False, str(exc)
                        previous_hash = digest
                        expected_seq += 1
            except OSError as exc:
                return False, f"Chronicle read failure: {exc}"

        return True, None
