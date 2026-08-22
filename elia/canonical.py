from __future__ import annotations

import json
import math
from typing import Any


def _validate_json_value(
    value: Any,
    *,
    path: str,
    active_containers: set[int],
    depth: int,
) -> None:
    """Reject lossy/non-deterministic coercions before integrity hashing."""

    if depth > 100:
        raise ValueError(f"canonical JSON nesting is too deep at {path}")
    value_type = type(value)
    if value is None or value_type in {str, bool, int}:
        return
    if value_type is float:
        if not math.isfinite(value):
            raise ValueError(f"canonical JSON rejects non-finite float at {path}")
        return
    if value_type not in {list, tuple, dict}:
        raise TypeError(
            "canonical JSON rejects non-JSON value at "
            f"{path}: {value_type.__module__}.{value_type.__qualname__}"
        )
    identity = id(value)
    if identity in active_containers:
        raise ValueError(f"canonical JSON rejects a circular container at {path}")
    active_containers.add(identity)
    try:
        if value_type is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise TypeError(
                        "canonical JSON requires string object keys at "
                        f"{path}: got {type(key).__module__}.{type(key).__qualname__}"
                    )
                _validate_json_value(
                    item,
                    path=f"{path}.{key}",
                    active_containers=active_containers,
                    depth=depth + 1,
                )
        else:
            for index, item in enumerate(value):
                _validate_json_value(
                    item,
                    path=f"{path}[{index}]",
                    active_containers=active_containers,
                    depth=depth + 1,
                )
    finally:
        active_containers.remove(identity)


def validate_json_value(value: Any) -> None:
    """Validate the exact finite JSON data model without serializing it.

    Versioned formats whose historical byte encoding cannot change use this entry
    point before applying their existing deterministic serializer.
    """

    _validate_json_value(
        value,
        path="$",
        active_containers=set(),
        depth=0,
    )


def strict_json_loads(payload: str | bytes | bytearray) -> Any:
    """Parse strict JSON, rejecting duplicate names and non-finite constants."""

    def reject_constant(token: str) -> None:
        raise ValueError(f"strict JSON rejects non-finite constant {token}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"strict JSON rejects duplicate object key {key!r}")
            result[key] = value
        return result

    value = json.loads(
        payload,
        parse_constant=reject_constant,
        object_pairs_hook=unique_object,
    )
    validate_json_value(value)
    return value


def canonical_json(value: Any) -> str:
    """Serialize the strict JSON data model without silent ``str()`` coercion.

    This function is intended for hashes, signatures, idempotency keys and other
    integrity boundaries. Tuples intentionally share JSON array semantics with lists;
    unsupported objects, non-string mapping keys, cycles and NaN/Infinity fail closed.
    """

    validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_json_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")
