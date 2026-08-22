from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from typing import Any


class BodyInputError(ValueError):
    pass


def bounded_json_value(
    value: Any,
    *,
    field: str = "value",
    max_bytes: int = 512_000,
    max_depth: int = 12,
    max_items: int = 1024,
) -> Any:
    """Return a JSON-only bounded copy, rejecting non-finite and oversized values."""

    seen = 0

    def visit(item: Any, path: str, depth: int) -> Any:
        nonlocal seen
        seen += 1
        if seen > max_items:
            raise BodyInputError(f"{field} exceeds aggregate item limit")
        if depth > max_depth:
            raise BodyInputError(f"{field} exceeds nesting depth")
        if item is None or isinstance(item, (str, bool)):
            return item
        if isinstance(item, int) and not isinstance(item, bool):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise BodyInputError(f"{path} must be finite")
            return item
        if isinstance(item, list):
            return [visit(child, f"{path}[{index}]", depth + 1) for index, child in enumerate(item)]
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for raw_key, child in item.items():
                if not isinstance(raw_key, str) or not raw_key or len(raw_key) > 256:
                    raise BodyInputError(f"{path} contains an invalid field name")
                result[raw_key] = visit(child, f"{path}.{raw_key}", depth + 1)
            return result
        raise BodyInputError(f"{path} contains a non-JSON value")

    result = visit(value, field, 0)
    try:
        encoded = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BodyInputError(f"{field} is not canonical JSON") from exc
    if len(encoded) > max(1, int(max_bytes)):
        raise BodyInputError(f"{field} exceeds byte limit")
    return result


def validate_json_schema(value: Any, schema: Any, *, field: str) -> Any:
    """Validate the strict, intentionally small schema dialect used for body scopes."""

    if not isinstance(schema, dict) or not schema:
        raise BodyInputError(f"{field} has no configured argument schema")
    bounded = bounded_json_value(value, field=field)

    def check(item: Any, spec: dict[str, Any], path: str) -> None:
        expected = spec.get("type")
        type_ok = {
            "object": isinstance(item, dict),
            "array": isinstance(item, list),
            "string": isinstance(item, str),
            "integer": isinstance(item, int) and not isinstance(item, bool),
            "number": isinstance(item, (int, float)) and not isinstance(item, bool),
            "boolean": isinstance(item, bool),
            "null": item is None,
        }
        expected_types = list(expected) if isinstance(expected, list) else [expected]
        if not expected_types or not any(
            candidate in type_ok and type_ok[candidate] for candidate in expected_types
        ):
            raise BodyInputError(f"{path} does not match configured type {expected!r}")
        if "enum" in spec and item not in list(spec.get("enum") or []):
            raise BodyInputError(f"{path} is outside configured enum")
        if isinstance(item, str):
            if len(item) < int(spec.get("minLength", 0)):
                raise BodyInputError(f"{path} is shorter than configured minimum")
            if len(item) > int(spec.get("maxLength", 32_000)):
                raise BodyInputError(f"{path} is longer than configured maximum")
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            if "minimum" in spec and item < spec["minimum"]:
                raise BodyInputError(f"{path} is below configured minimum")
            if "maximum" in spec and item > spec["maximum"]:
                raise BodyInputError(f"{path} exceeds configured maximum")
        if isinstance(item, list):
            if len(item) > int(spec.get("maxItems", 128)):
                raise BodyInputError(f"{path} has too many items")
            item_schema = spec.get("items")
            if not isinstance(item_schema, dict):
                raise BodyInputError(f"{path} has no configured item schema")
            for index, child in enumerate(item):
                check(child, item_schema, f"{path}[{index}]")
        if isinstance(item, dict):
            properties = spec.get("properties") or {}
            if not isinstance(properties, dict):
                raise BodyInputError(f"{path} properties schema is invalid")
            required = {str(name) for name in spec.get("required") or []}
            missing = required - set(item)
            if missing:
                raise BodyInputError(f"{path} is missing required fields: {sorted(missing)}")
            unknown = set(item) - set(properties)
            if unknown and spec.get("additionalProperties") is not True:
                raise BodyInputError(f"{path} contains out-of-scope fields: {sorted(unknown)}")
            for name, child in item.items():
                child_schema = properties.get(name)
                if child_schema is None:
                    continue
                if not isinstance(child_schema, dict):
                    raise BodyInputError(f"{path}.{name} schema is invalid")
                check(child, child_schema, f"{path}.{name}")

    check(bounded, schema, field)
    return bounded


@dataclass(frozen=True, slots=True)
class BodyCapability:
    name: str
    description: str
    args: str
    authority: str
    side_effects: str
    network_scope: str
    cost_class: str
    enabled: bool
    readiness: str = "ready"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BodyResult:
    ok: bool
    capability: str
    data: Any = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
