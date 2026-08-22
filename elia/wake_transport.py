from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hmac
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Literal
from uuid import uuid4

from .canonical import canonical_json_bytes


CHECKPOINT_NAME = "elia-genesis.eliacp"
DIGEST_NAME = "trusted-digest.txt"
TRANSPORT_NAME = "transport-state.json"
RELAY_REPORT_NAME = "relay-report.json"
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

KernelState = Literal["unknown", "queued", "running", "complete", "failed"]
DatasetState = Literal["unknown", "pending", "ready", "failed"]


@dataclass(slots=True)
class TransportState:
    version: int = 2
    pending_launch_nonce: str | None = None
    pending_since: str | None = None
    consecutive_kernel_failures: int = 0
    last_success_digest: str | None = None
    last_success_counter: int | None = None
    last_kernel_status: str | None = None
    last_error: str | None = None
    operator_reset_count: int = 0
    last_operator_reset_at: str | None = None
    last_operator_reset_evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if type(self.version) is not int or self.version not in {1, 2}:
            raise ValueError("transport version must be integer 1 or 2")
        for name in ("consecutive_kernel_failures", "operator_reset_count"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"transport {name} must be a non-negative integer")
        if self.last_success_counter is not None and (
            type(self.last_success_counter) is not int or self.last_success_counter < 1
        ):
            raise ValueError("transport last_success_counter must be a positive integer")
        if self.pending_launch_nonce is not None:
            if (
                type(self.pending_launch_nonce) is not str
                or re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", self.pending_launch_nonce)
                is None
            ):
                raise ValueError("transport pending nonce is invalid")
        _validate_optional_timestamp("pending_since", self.pending_since)
        if (self.pending_launch_nonce is None) != (self.pending_since is None):
            raise ValueError("transport pending nonce and timestamp must occur together")
        if self.last_success_digest is not None:
            if type(self.last_success_digest) is not str:
                raise ValueError("transport last_success_digest must be a string")
            self.last_success_digest = validate_digest(self.last_success_digest)
        if (self.last_success_digest is None) != (self.last_success_counter is None):
            raise ValueError("transport success digest and counter must occur together")
        for name, maximum in (("last_kernel_status", 128), ("last_error", 4000)):
            value = getattr(self, name)
            if value is not None and (
                type(value) is not str or not value or len(value) > maximum
            ):
                raise ValueError(f"transport {name} is invalid")
        _validate_optional_timestamp("last_operator_reset_at", self.last_operator_reset_at)
        if self.last_operator_reset_evidence_sha256 is not None:
            if type(self.last_operator_reset_evidence_sha256) is not str:
                raise ValueError("transport reset evidence digest must be a string")
            self.last_operator_reset_evidence_sha256 = validate_digest(
                self.last_operator_reset_evidence_sha256
            )
        has_reset_evidence = (
            self.last_operator_reset_at is not None
            and self.last_operator_reset_evidence_sha256 is not None
        )
        if self.operator_reset_count == 0 and (
            self.last_operator_reset_at is not None
            or self.last_operator_reset_evidence_sha256 is not None
        ):
            raise ValueError("transport reset evidence requires a positive reset count")
        if self.operator_reset_count > 0 and not has_reset_evidence:
            raise ValueError("transport reset count requires timestamp and evidence digest")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> "TransportState":
        allowed = set(cls.__dataclass_fields__)
        unknown = set(item) - allowed
        if unknown:
            raise ValueError(f"transport state contains unknown fields: {sorted(unknown)}")
        return cls(
            version=item.get("version", 1),
            pending_launch_nonce=item.get("pending_launch_nonce"),
            pending_since=item.get("pending_since"),
            consecutive_kernel_failures=item.get("consecutive_kernel_failures", 0),
            last_success_digest=item.get("last_success_digest"),
            last_success_counter=item.get("last_success_counter"),
            last_kernel_status=item.get("last_kernel_status"),
            last_error=item.get("last_error"),
            operator_reset_count=item.get("operator_reset_count", 0),
            last_operator_reset_at=item.get("last_operator_reset_at"),
            last_operator_reset_evidence_sha256=item.get(
                "last_operator_reset_evidence_sha256"
            ),
        )


def _validate_optional_timestamp(name: str, value: str | None) -> None:
    if value is None:
        return
    if type(value) is not str or not value or len(value) > 128:
        raise ValueError(f"transport {name} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"transport {name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"transport {name} must include a timezone")


def validate_digest(value: str) -> str:
    digest = str(value).strip().lower()
    if not DIGEST_RE.fullmatch(digest):
        raise ValueError("trusted digest must be exactly 64 lowercase/uppercase hex characters")
    return digest


def read_digest(path: Path) -> str:
    return validate_digest(Path(path).read_text(encoding="utf-8"))


def write_digest(path: Path, digest: str) -> None:
    Path(path).write_text(validate_digest(digest) + "\n", encoding="utf-8")


def _transport_key(key: bytes | str | None) -> bytes | None:
    if key is None:
        return None
    raw = key.encode("utf-8") if isinstance(key, str) else bytes(key)
    if len(raw) < 16:
        raise ValueError("transport authentication key must be at least 16 bytes")
    return raw


def _canonical_transport(state: TransportState) -> bytes:
    state.validate()
    return canonical_json_bytes(state.as_dict())


def _transport_signature(state: TransportState, key: bytes) -> str:
    return hmac.new(
        key,
        b"ELIA-WAKE-TRANSPORT-V2\x00" + _canonical_transport(state),
        sha256,
    ).hexdigest()


def read_transport_state(
    path: Path,
    *,
    key: bytes | str | None = None,
    require_auth: bool = False,
) -> TransportState:
    path = Path(path)
    if not path.exists():
        if require_auth:
            raise FileNotFoundError("authenticated transport state is missing")
        return TransportState()
    item = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(item, dict):
        raise ValueError("transport state must be a JSON object")
    raw_key = _transport_key(key)
    if "transport" in item or "hmac_sha256" in item:
        if item.get("schema") != "elia-wake-transport-v2":
            raise ValueError("unsupported authenticated transport schema")
        payload = item.get("transport")
        signature = str(item.get("hmac_sha256", "")).strip().lower()
        if not isinstance(payload, dict):
            raise ValueError("authenticated transport envelope requires a transport object")
        if raw_key is None:
            raise ValueError("transport authentication key is required")
        if not DIGEST_RE.fullmatch(signature):
            raise PermissionError("transport state authentication failed")
        state = TransportState.from_dict(payload)
        expected = _transport_signature(state, raw_key)
        if not hmac.compare_digest(signature, expected):
            raise PermissionError("transport state authentication failed")
        return state
    if require_auth:
        raise PermissionError("legacy unauthenticated transport state is not accepted")
    return TransportState.from_dict(item)


def write_transport_state(
    path: Path,
    state: TransportState,
    *,
    key: bytes | str | None = None,
    require_auth: bool = False,
) -> None:
    path = Path(path)
    state.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw_key = _transport_key(key)
    if require_auth and raw_key is None:
        raise ValueError("transport authentication key is required")
    payload: dict[str, Any]
    if raw_key is None:
        payload = state.as_dict()
    else:
        payload = {
            "schema": "elia-wake-transport-v2",
            "transport": state.as_dict(),
            "hmac_sha256": _transport_signature(state, raw_key),
        }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def locate_unique(root: Path, filename: str) -> Path:
    matches = [path for path in Path(root).rglob(filename) if path.is_file()]
    if len(matches) != 1:
        raise FileNotFoundError(
            f"expected exactly one {filename!r} under {root}, found {len(matches)}"
        )
    return matches[0]


def locate_state_bundle(root: Path) -> tuple[Path, Path, Path | None]:
    checkpoint = locate_unique(root, CHECKPOINT_NAME)
    digest = locate_unique(root, DIGEST_NAME)
    transport_matches = [path for path in Path(root).rglob(TRANSPORT_NAME) if path.is_file()]
    if len(transport_matches) > 1:
        raise FileNotFoundError(
            f"expected at most one {TRANSPORT_NAME!r}, found {len(transport_matches)}"
        )
    return checkpoint, digest, transport_matches[0] if transport_matches else None


def parse_kernel_status(output: str) -> KernelState:
    """Classify Kaggle CLI status text without depending on one exact rendering."""
    text = " ".join(str(output).strip().lower().split())
    if not text:
        return "unknown"
    failure_tokens = ("error", "failed", "failure", "cancelled", "canceled")
    complete_tokens = ("complete", "completed", "success", "succeeded")
    running_tokens = ("running", "executing", "active")
    queued_tokens = ("queued", "pending", "starting", "preparing")
    if any(token in text for token in failure_tokens):
        return "failed"
    if any(token in text for token in complete_tokens):
        return "complete"
    if any(token in text for token in running_tokens):
        return "running"
    if any(token in text for token in queued_tokens):
        return "queued"
    return "unknown"


def _flatten_status_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if "status" in str(key).lower() or "state" in str(key).lower():
                values.extend(_flatten_status_values(item))
            elif isinstance(item, (dict, list)):
                values.extend(_flatten_status_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(_flatten_status_values(item))
    elif value is not None:
        values.append(str(value))
    return values


def parse_dataset_status(output: str) -> DatasetState:
    """Classify `kaggle datasets status`, preferring its JSON status/state fields."""
    raw = str(output).strip()
    if not raw:
        return "unknown"
    candidates: list[str] = []
    try:
        item = json.loads(raw)
    except json.JSONDecodeError:
        item = None
    if item is not None:
        candidates.extend(_flatten_status_values(item))
    candidates.append(raw)
    text = " ".join(candidates).lower()

    failure_tokens = (
        "error",
        "failed",
        "failure",
        "cancelled",
        "canceled",
        "invalid",
        "rejected",
    )
    ready_tokens = (
        "ready",
        "complete",
        "completed",
        "success",
        "succeeded",
        "active",
    )
    pending_tokens = (
        "pending",
        "queued",
        "running",
        "processing",
        "creating",
        "updating",
    )
    if any(token in text for token in failure_tokens):
        return "failed"
    if any(token in text for token in ready_tokens):
        return "ready"
    if any(token in text for token in pending_tokens):
        return "pending"
    return "unknown"


def new_launch_nonce() -> str:
    return uuid4().hex


def mark_pending(state: TransportState, nonce: str | None = None) -> TransportState:
    nonce = nonce or new_launch_nonce()
    return TransportState(
        version=state.version,
        pending_launch_nonce=nonce,
        pending_since=datetime.now(timezone.utc).isoformat(),
        consecutive_kernel_failures=state.consecutive_kernel_failures,
        last_success_digest=state.last_success_digest,
        last_success_counter=state.last_success_counter,
        last_kernel_status="launching",
        last_error=None,
        operator_reset_count=state.operator_reset_count,
        last_operator_reset_at=state.last_operator_reset_at,
        last_operator_reset_evidence_sha256=state.last_operator_reset_evidence_sha256,
    )


def mark_failure(state: TransportState, error: str, status: str = "failed") -> TransportState:
    return TransportState(
        version=state.version,
        pending_launch_nonce=None,
        pending_since=None,
        consecutive_kernel_failures=state.consecutive_kernel_failures + 1,
        last_success_digest=state.last_success_digest,
        last_success_counter=state.last_success_counter,
        last_kernel_status=status[:128],
        last_error=str(error)[:4000],
        operator_reset_count=state.operator_reset_count,
        last_operator_reset_at=state.last_operator_reset_at,
        last_operator_reset_evidence_sha256=state.last_operator_reset_evidence_sha256,
    )


def mark_success(state: TransportState, digest: str, counter: int) -> TransportState:
    if type(counter) is not int or counter < 1:
        raise ValueError("successful transport counter must be a positive integer")
    return TransportState(
        version=state.version,
        pending_launch_nonce=None,
        pending_since=None,
        consecutive_kernel_failures=0,
        last_success_digest=validate_digest(digest),
        last_success_counter=counter,
        last_kernel_status="complete",
        last_error=None,
        operator_reset_count=state.operator_reset_count,
        last_operator_reset_at=state.last_operator_reset_at,
        last_operator_reset_evidence_sha256=state.last_operator_reset_evidence_sha256,
    )


def mark_operator_reset(state: TransportState, *, evidence: str) -> TransportState:
    evidence_text = str(evidence).strip()
    if not evidence_text:
        raise ValueError("operator circuit reset requires non-empty evidence")
    if state.pending_launch_nonce:
        raise ValueError("operator circuit reset is forbidden while a launch is pending")
    if not launch_suppressed(state):
        raise ValueError("operator circuit reset requires a suppressed transport")
    return TransportState(
        version=max(2, state.version),
        pending_launch_nonce=None,
        pending_since=None,
        consecutive_kernel_failures=0,
        last_success_digest=state.last_success_digest,
        last_success_counter=state.last_success_counter,
        last_kernel_status="operator_reset",
        last_error=None,
        operator_reset_count=state.operator_reset_count + 1,
        last_operator_reset_at=datetime.now(timezone.utc).isoformat(),
        last_operator_reset_evidence_sha256=sha256(evidence_text.encode("utf-8")).hexdigest(),
    )


def launch_suppressed(state: TransportState, threshold: int = 3) -> bool:
    return state.consecutive_kernel_failures >= max(1, int(threshold))


def build_kernel_metadata(
    *,
    kernel_id: str,
    state_dataset: str,
    code_file: str = "elia_wild_runner.py",
    accelerator: str = "NvidiaTeslaT4",
) -> dict[str, Any]:
    if "/" not in kernel_id or kernel_id.startswith("/") or kernel_id.endswith("/"):
        raise ValueError("kernel_id must be owner/kernel-slug")
    if "/" not in state_dataset or state_dataset.startswith("/") or state_dataset.endswith("/"):
        raise ValueError("state_dataset must be owner/dataset-slug")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", accelerator):
        raise ValueError("invalid Kaggle accelerator identifier")
    return {
        "id": kernel_id,
        "title": "ELIA WILD Genesis Runner",
        "code_file": code_file,
        "language": "python",
        "kernel_type": "script",
        "is_private": "true",
        "enable_gpu": "true",
        "enable_internet": "true",
        "machine_shape": accelerator,
        "dataset_sources": [state_dataset],
        "competition_sources": [],
        "kernel_sources": [],
        "model_sources": [],
    }


def render_runner(template: str, wake_config: dict[str, Any]) -> str:
    marker = "__ELIA_WAKE_CONFIG__"
    if template.count(marker) != 1:
        raise ValueError("runner template must contain exactly one wake-config marker")
    serialized = json.dumps(wake_config, ensure_ascii=False, sort_keys=True)
    return template.replace(marker, serialized)


def validate_relay_report(
    report: dict[str, Any], *, expected_nonce: str, expected_source_digest: str
) -> tuple[str, int]:
    if str(report.get("launch_nonce", "")) != expected_nonce:
        raise ValueError("relay report launch nonce does not match pending launch")
    source = validate_digest(str(report.get("source_digest", "")))
    if source != validate_digest(expected_source_digest):
        raise ValueError("relay report source digest does not match launched state")
    output = validate_digest(str(report.get("output_digest", "")))
    counter = int(report.get("output_counter", 0))
    if counter <= 0:
        raise ValueError("relay report output counter must be positive")
    return output, counter
