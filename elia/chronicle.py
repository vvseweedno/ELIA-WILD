from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import os
from typing import Any, Iterator

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
    """Append-only JSONL history with SHA-256 chaining and POSIX single-writer locking."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    @staticmethod
    def _digest(seq: int, timestamp: str, kind: str, payload: dict[str, Any], previous_hash: str) -> str:
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
        with self._locked(exclusive=True):
            last_seq, previous_hash = self._last_unlocked()
            seq = last_seq + 1
            timestamp = datetime.now(timezone.utc).isoformat()
            digest = self._digest(seq, timestamp, kind, payload, previous_hash)
            entry = ChronicleEntry(seq, timestamp, kind, payload, previous_hash, digest)
            serialized = json.dumps(asdict(entry), ensure_ascii=False, sort_keys=True) + "\n"
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            return entry

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
                            item = json.loads(line)
                            seq = int(item["seq"])
                            timestamp = str(item["timestamp"])
                            kind = str(item["kind"])
                            payload = dict(item["payload"])
                            item_previous = str(item["previous_hash"])
                            item_hash = str(item["hash"])
                        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                            return False, f"malformed entry at line {line_number}: {exc}"
                        if seq != expected_seq:
                            return False, f"sequence mismatch at line {line_number}"
                        if item_previous != previous_hash:
                            return False, f"previous_hash mismatch at line {line_number}"
                        digest = self._digest(seq, timestamp, kind, payload, item_previous)
                        if digest != item_hash:
                            return False, f"hash mismatch at line {line_number}"
                        previous_hash = digest
                        expected_seq += 1
            except OSError as exc:
                return False, f"Chronicle read failure: {exc}"

        return True, None
