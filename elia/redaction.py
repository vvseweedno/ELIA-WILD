from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any


def fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def safe_action_descriptor(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"name": str(value)[:128], "arguments_fingerprint": fingerprint({})}
    name = str(value.get("name", ""))[:128]
    if "args" in value:
        args = value.get("args") if isinstance(value.get("args"), dict) else {}
        return {
            "name": name,
            "argument_keys": sorted(str(key) for key in args)[:64],
            "arguments_fingerprint": fingerprint(args),
        }
    # Already-redacted descriptors remain stable instead of being fingerprinted again.
    return {
        "name": name,
        "argument_keys": list(value.get("argument_keys") or [])[:64],
        "arguments_fingerprint": str(value.get("arguments_fingerprint") or fingerprint({}))[:64],
    }


def safe_observation_ref(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = (
        "id",
        "transaction_id",
        "payload_sha256",
        "source_kind",
        "source_ref",
        "success",
        "summary",
    )
    result = {key: deepcopy(value[key]) for key in allowed if key in value}
    if "summary" in result:
        result["summary"] = str(result["summary"])[:1000]
    return result or None


def safe_tool_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "ok": False,
            "tool": "unknown",
            "error": None,
            "result_fingerprint": fingerprint(value),
        }
    result: dict[str, Any] = {
        "ok": bool(value.get("ok", False)),
        "tool": str(value.get("tool", value.get("capability", "unknown")))[:128],
        "error": str(value["error"])[:1000] if value.get("error") else None,
    }
    observation = safe_observation_ref(value.get("observation"))
    if observation is not None:
        result["observation"] = observation
    if "data" in value:
        data = value.get("data")
        result["data_fingerprint"] = fingerprint(data)
        result["data_keys"] = (
            sorted(str(key) for key in data)[:64] if isinstance(data, dict) else []
        )
    if "result_fingerprint" in value:
        result["result_fingerprint"] = str(value["result_fingerprint"])[:64]
    return result


def redact_action_record(value: Any) -> Any:
    """Remove raw action arguments/tool payloads from durable action records.

    This function is deliberately idempotent so a record may be redacted at multiple
    persistence boundaries without changing its semantic fingerprints.
    """
    if not isinstance(value, dict):
        return value
    item = deepcopy(value)
    proposed = item.get("proposed")
    if isinstance(proposed, dict) and "action" in proposed:
        proposed["action"] = safe_action_descriptor(proposed.get("action"))
    if "action" in item:
        item["action"] = safe_action_descriptor(item.get("action"))
    if "result" in item:
        item["result"] = safe_tool_result(item.get("result"))
    return item


def redact_serialized_action_record(value: str) -> str:
    try:
        item = json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return str(value)
    redacted = redact_action_record(item)
    return json.dumps(redacted, ensure_ascii=False, sort_keys=True)
