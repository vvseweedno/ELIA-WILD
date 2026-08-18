from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import os
from typing import Any, Iterator

from .redaction import redact_action_record

try:  # Linux is the production/runtime target; keep import optional for tooling portability.
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - non-POSIX fallback has no cross-process guarantee.
    fcntl = None


GENESIS_HASH = "0" * 64


@dataclass(slots=True)
class ChronicleEntry:
    seq: int
    timestamp: str
    kind: str
    payload: dict[str, Any]
    previous_hash: str
    hash: str


class Chronicle:
    """Append-only JSONL history with SHA-256 chaining and POSIX single-writer locking.

    Cycle records are redacted again at this final persistence boundary so an older or
    alternate runtime implementation cannot accidentally write raw action arguments or
    tool payloads into the durable identity history.

    A valid current chain is not by itself proof of continuity with an older accepted
    chain. `hash_at_seq` / `contains_anchor` expose exact prefix ancestry so CRC/vitals
    can prove that an earlier accepted head is still present in the current history.
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
        canonical = json.dumps(
            {
                "seq": seq,
                "timestamp": timestamp,
                "kind": kind,
                "payload": payload,
                "previous_hash": previous_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
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
        last_line = ""
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last_line = line
        if not last_line:
            return 0, GENESIS_HASH
        item = json.loads(last_line)
        return int(item["seq"]), str(item["hash"])

    def head(self) -> tuple[int, str]:
        """Return the current sequence/hash head without mutating the Chronicle."""
        with self._locked(exclusive=False):
            try:
                return self._last_unlocked()
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"Chronicle head is unreadable: {exc}") from exc

    def append(self, kind: str, payload: dict[str, Any]) -> ChronicleEntry:
        persisted_payload = (
            redact_action_record(payload) if str(kind).upper() == "CYCLE" else payload
        )
        with self._locked(exclusive=True):
            last_seq, previous_hash = self._last_unlocked()
            seq = last_seq + 1
            timestamp = datetime.now(timezone.utc).isoformat()
            digest = self._digest(seq, timestamp, kind, persisted_payload, previous_hash)
            entry = ChronicleEntry(
                seq, timestamp, kind, persisted_payload, previous_hash, digest
            )
            serialized = json.dumps(asdict(entry), ensure_ascii=False, sort_keys=True) + "\n"
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
            item = json.loads(line)
            seq = int(item["seq"])
            timestamp = str(item["timestamp"])
            kind = str(item["kind"])
            payload = dict(item["payload"])
            item_previous = str(item["previous_hash"])
            item_hash = str(item["hash"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed entry at line {line_number}: {exc}") from exc
        if seq != expected_seq:
            raise ValueError(f"sequence mismatch at line {line_number}")
        if item_previous != previous_hash:
            raise ValueError(f"previous_hash mismatch at line {line_number}")
        digest = Chronicle._digest(seq, timestamp, kind, payload, item_previous)
        if digest != item_hash:
            raise ValueError(f"hash mismatch at line {line_number}")
        return seq, digest

    def hash_at_seq(self, seq: int) -> str:
        """Return the validated hash at exactly `seq`, or fail closed.

        Sequence zero is the immutable genesis anchor. For positive sequences the
        method validates the chain from genesis through the requested point rather
        than trusting a raw line lookup.
        """
        seq = int(seq)
        if seq < 0:
            raise ValueError("Chronicle sequence must be non-negative")
        if seq == 0:
            return GENESIS_HASH
        if not self.path.exists():
            raise LookupError(f"Chronicle has no sequence {seq}")
        previous_hash = GENESIS_HASH
        expected_seq = 1
        with self._locked(exclusive=False):
            try:
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
            except OSError as exc:
                raise RuntimeError(f"Chronicle read failure: {exc}") from exc
        raise LookupError(f"Chronicle has no sequence {seq}")

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
