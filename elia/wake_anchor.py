from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


ANCHOR_VERSION = 1
DEFAULT_ANCHOR_PATH = Path("~/.local/state/elia-wild/kaggle-trust-anchor.json")


class WakeTrustAnchorError(RuntimeError):
    pass


class WakeTrustAnchorRollbackError(WakeTrustAnchorError):
    pass


@dataclass(frozen=True, slots=True)
class WakeTrustAnchor:
    version: int
    identity_name: str
    state_dataset: str
    counter: int
    digest: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_anchor_path() -> Path:
    raw = os.getenv("ELIA_KAGGLE_TRUST_ANCHOR", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_ANCHOR_PATH.expanduser()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


class WakeTrustAnchorStore:
    """Tamper-evident rollback anchor stored outside the Kaggle state Dataset.

    The state Dataset carries the encrypted checkpoint plus transport metadata. That is
    not enough to detect replay of an older *entire* Dataset version. This store binds
    the last accepted checkpoint counter/digest to a separate durable relay witness,
    authenticated with the checkpoint HMAC key.

    Source verification and forward advancement are intentionally separate. A relay
    restored from an older external witness must never silently learn a newer truth from
    the Dataset it is supposed to police; that condition is fail-closed and requires
    recovery of the missing witness. Only a checkpoint that has already passed relay
    validation may advance the anchor.
    """

    def __init__(
        self,
        path: Path,
        *,
        key: bytes,
        identity_name: str,
        state_dataset: str,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        if len(key) < 16:
            raise ValueError("wake trust-anchor key must be at least 16 bytes")
        self.key = bytes(key)
        self.identity_name = str(identity_name).strip()
        self.state_dataset = str(state_dataset).strip()
        if not self.identity_name or "/" not in self.state_dataset:
            raise ValueError("identity_name and owner/dataset state_dataset are required")

    def _signature(self, payload: dict[str, Any]) -> str:
        return hmac.new(self.key, _canonical_json(payload), sha256).hexdigest()

    def read(self) -> WakeTrustAnchor | None:
        if not self.path.exists():
            return None
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WakeTrustAnchorError(f"invalid wake trust anchor: {exc}") from exc
        if not isinstance(envelope, dict):
            raise WakeTrustAnchorError("wake trust anchor must contain a JSON object")
        payload = envelope.get("anchor")
        signature = str(envelope.get("hmac_sha256") or "")
        if not isinstance(payload, dict) or not signature:
            raise WakeTrustAnchorError("wake trust anchor is missing payload/signature")
        expected = self._signature(payload)
        if not hmac.compare_digest(signature, expected):
            raise WakeTrustAnchorError("wake trust anchor authentication failed")
        try:
            anchor = WakeTrustAnchor(
                version=int(payload["version"]),
                identity_name=str(payload["identity_name"]),
                state_dataset=str(payload["state_dataset"]),
                counter=int(payload["counter"]),
                digest=str(payload["digest"]).strip().lower(),
                updated_at=str(payload["updated_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WakeTrustAnchorError(f"invalid wake trust anchor fields: {exc}") from exc
        if anchor.version != ANCHOR_VERSION:
            raise WakeTrustAnchorError(
                f"unsupported wake trust-anchor version: {anchor.version}"
            )
        if anchor.identity_name != self.identity_name:
            raise WakeTrustAnchorError("wake trust anchor identity mismatch")
        if anchor.state_dataset != self.state_dataset:
            raise WakeTrustAnchorError("wake trust anchor Dataset mismatch")
        if anchor.counter < 1 or len(anchor.digest) != 64:
            raise WakeTrustAnchorError("wake trust anchor counter/digest is invalid")
        return anchor

    def _write(self, counter: int, digest: str) -> WakeTrustAnchor:
        digest = str(digest).strip().lower()
        if int(counter) < 1 or len(digest) != 64:
            raise WakeTrustAnchorError("refusing invalid trust-anchor counter/digest")
        anchor = WakeTrustAnchor(
            version=ANCHOR_VERSION,
            identity_name=self.identity_name,
            state_dataset=self.state_dataset,
            counter=int(counter),
            digest=digest,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        payload = anchor.as_dict()
        envelope = {
            "anchor": payload,
            "hmac_sha256": self._signature(payload),
        }
        _atomic_write(
            self.path,
            json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n",
        )
        return anchor

    def initialize(self, *, counter: int, digest: str) -> WakeTrustAnchor:
        if self.path.exists():
            current = self.read()
            if current is None:
                raise WakeTrustAnchorError("wake trust anchor unexpectedly disappeared")
            self.verify(counter=counter, digest=digest)
            return current
        return self._write(counter, digest)

    def verify(self, *, counter: int, digest: str) -> WakeTrustAnchor:
        """Require the candidate to equal the independently persisted trusted state."""

        current = self.read()
        if current is None:
            raise WakeTrustAnchorError(
                "wake trust anchor is missing; initialize it during trusted state bootstrap"
            )
        counter = int(counter)
        digest = str(digest).strip().lower()
        if counter < current.counter:
            raise WakeTrustAnchorRollbackError(
                f"state rollback detected: counter {counter} < trusted {current.counter}"
            )
        if counter > current.counter:
            raise WakeTrustAnchorError(
                "state is ahead of the durable wake trust anchor; recover the latest external "
                "anchor before accepting Dataset state"
            )
        if not hmac.compare_digest(digest, current.digest):
            raise WakeTrustAnchorRollbackError(
                "state fork/replay detected at the trusted checkpoint counter"
            )
        return current

    def advance(self, *, counter: int, digest: str) -> WakeTrustAnchor:
        """Advance after a newly validated checkpoint has become durable externally."""

        current = self.read()
        if current is None:
            raise WakeTrustAnchorError(
                "wake trust anchor is missing; initialize it during trusted state bootstrap"
            )
        counter = int(counter)
        digest = str(digest).strip().lower()
        if counter < current.counter:
            raise WakeTrustAnchorRollbackError(
                f"state rollback detected: counter {counter} < trusted {current.counter}"
            )
        if counter == current.counter:
            return self.verify(counter=counter, digest=digest)
        return self._write(counter, digest)

    def accept(self, *, counter: int, digest: str) -> WakeTrustAnchor:
        """Compatibility alias for trusted forward acceptance; prefer verify/advance."""

        return self.advance(counter=counter, digest=digest)
