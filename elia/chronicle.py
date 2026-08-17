from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
from typing import Any


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
    """Append-only JSONL history with a SHA-256 hash chain."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

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

    def _last(self) -> tuple[int, str]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return 0, GENESIS_HASH
        last_line = ""
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last_line = line
        item = json.loads(last_line)
        return int(item["seq"]), str(item["hash"])

    def head(self) -> tuple[int, str]:
        """Return the current sequence/hash head without mutating the Chronicle."""
        return self._last()

    def append(self, kind: str, payload: dict[str, Any]) -> ChronicleEntry:
        last_seq, previous_hash = self._last()
        seq = last_seq + 1
        timestamp = datetime.now(timezone.utc).isoformat()
        digest = self._digest(seq, timestamp, kind, payload, previous_hash)
        entry = ChronicleEntry(seq, timestamp, kind, payload, previous_hash, digest)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(entry), ensure_ascii=False, sort_keys=True) + "\n")
        return entry

    def verify(self) -> tuple[bool, str | None]:
        previous_hash = GENESIS_HASH
        expected_seq = 1
        if not self.path.exists():
            return True, None

        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                item = json.loads(line)
                if int(item["seq"]) != expected_seq:
                    return False, f"sequence mismatch at line {line_number}"
                if item["previous_hash"] != previous_hash:
                    return False, f"previous_hash mismatch at line {line_number}"
                digest = self._digest(
                    int(item["seq"]),
                    str(item["timestamp"]),
                    str(item["kind"]),
                    dict(item["payload"]),
                    str(item["previous_hash"]),
                )
                if digest != item["hash"]:
                    return False, f"hash mismatch at line {line_number}"
                previous_hash = digest
                expected_seq += 1

        return True, None
