from __future__ import annotations

from copy import deepcopy
from typing import Any


# A remote model provider is a different trust boundary from ELIA's local runtime.
# Only explicitly projected sensor metadata may cross that boundary. Raw sensor payloads
# stay in the local Sensorium and are addressed by digest/observation id when needed.
_SENSOR_FIELDS = (
    "id",
    "observed_at",
    "transaction_id",
    "source_kind",
    "source_ref",
    "modality",
    "content_type",
    "trust",
    "success",
    "summary",
    "payload_sha256",
    "provenance",
)


def _sensor_metadata(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in value[:64]:
        if not isinstance(raw, dict):
            continue
        item = {key: deepcopy(raw[key]) for key in _SENSOR_FIELDS if key in raw}
        # Provenance is useful for reasoning, but never forward nested raw payloads if
        # an adapter accidentally placed them there.
        provenance = item.get("provenance")
        if isinstance(provenance, dict):
            item["provenance"] = {
                str(key): deepcopy(val)
                for key, val in provenance.items()
                if str(key).lower() not in {"payload", "content", "body", "raw", "secret", "token"}
            }
        result.append(item)
    return result


def provider_context(context: dict[str, Any]) -> dict[str, Any]:
    """Return the only context view allowed to leave the local trust boundary.

    Private/internal keys (leading underscore) are excluded. Sensorium raw payloads
    are replaced with metadata + cryptographic digests. Other already-public runtime
    structures are deep-copied so provider serialization cannot mutate local state.
    """

    public: dict[str, Any] = {}
    for key, value in context.items():
        name = str(key)
        if name.startswith("_"):
            continue
        if name == "sensorium":
            public[name] = _sensor_metadata(value)
            continue
        public[name] = deepcopy(value)
    return public
