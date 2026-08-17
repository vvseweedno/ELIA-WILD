from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Literal
from uuid import uuid4


CHECKPOINT_NAME = "elia-genesis.eliacp"
DIGEST_NAME = "trusted-digest.txt"
TRANSPORT_NAME = "transport-state.json"
RELAY_REPORT_NAME = "relay-report.json"
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

KernelState = Literal["unknown", "queued", "running", "complete", "failed"]
DatasetState = Literal["unknown", "pending", "ready", "failed"]


@dataclass(slots=True)
class TransportState:
    version: int = 1
    pending_launch_nonce: str | None = None
    pending_since: str | None = None
    consecutive_kernel_failures: int = 0
    last_success_digest: str | None = None
    last_success_counter: int | None = None
    last_kernel_status: str | None = None
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> "TransportState":
        return cls(
            version=int(item.get("version", 1)),
            pending_launch_nonce=(
                str(item["pending_launch_nonce"])
                if item.get("pending_launch_nonce")
                else None
            ),
            pending_since=str(item["pending_since"]) if item.get("pending_since") else None,
            consecutive_kernel_failures=max(
                0, int(item.get("consecutive_kernel_failures", 0) or 0)
            ),
            last_success_digest=(
                str(item["last_success_digest"]).lower()
                if item.get("last_success_digest")
                else None
            ),
            last_success_counter=(
                int(item["last_success_counter"])
                if item.get("last_success_counter") is not None
                else None
            ),
            last_kernel_status=(
                str(item["last_kernel_status"])
                if item.get("last_kernel_status")
                else None
            ),
            last_error=str(item["last_error"])[:4000] if item.get("last_error") else None,
        )


def validate_digest(value: str) -> str:
    digest = str(value).strip().lower()
    if not DIGEST_RE.fullmatch(digest):
        raise ValueError("trusted digest must be exactly 64 lowercase/uppercase hex characters")
    return digest


def read_digest(path: Path) -> str:
    return validate_digest(Path(path).read_text(encoding="utf-8"))


def write_digest(path: Path, digest: str) -> None:
    Path(path).write_text(validate_digest(digest) + "\n", encoding="utf-8")


def read_transport_state(path: Path) -> TransportState:
    path = Path(path)
    if not path.exists():
        return TransportState()
    item = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(item, dict):
        raise ValueError("transport state must be a JSON object")
    return TransportState.from_dict(item)


def write_transport_state(path: Path, state: TransportState) -> None:
    Path(path).write_text(
        json.dumps(state.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


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
    )


def mark_success(state: TransportState, digest: str, counter: int) -> TransportState:
    return TransportState(
        version=state.version,
        pending_launch_nonce=None,
        pending_since=None,
        consecutive_kernel_failures=0,
        last_success_digest=validate_digest(digest),
        last_success_counter=int(counter),
        last_kernel_status="complete",
        last_error=None,
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
