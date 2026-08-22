from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any


_REDACTED = "[REDACTED]"
_SECRET_FIELD_SUFFIXES = (
    "secret",
    "password",
    "passwd",
    "credential",
    "credentials",
    "authorization",
    "cookie",
    "apikey",
    "accesstoken",
    "refreshtoken",
    "bearertoken",
    "clientsecret",
    "privatekey",
    "hmackey",
    "encryptionkey",
    "signingkey",
    "emailaddress",
    "phonenumber",
    "mobilenumber",
    "homeaddress",
    "postaladdress",
    "streetaddress",
    "socialsecuritynumber",
    "nationalid",
    "passportnumber",
    "taxid",
    "dateofbirth",
    "accountnumber",
)
_BEARER_RE = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_SENSITIVE_QUERY_RE = re.compile(
    r"([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|"
    r"password|passwd|authorization|signature|sig)=)[^&#\s]+",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"\b(api[_-]?key|access[_-]?token|refresh[_-]?token|bearer[_-]?token|"
    r"client[_-]?secret|secret|password|passwd|authorization|cookie)"
    r"(\s*[:=]\s*)[^\s,;&]+",
    re.IGNORECASE,
)
_CREDENTIAL_URL_RE = re.compile(
    r"\b([a-z][a-z0-9+.-]*://)[^/@\s:]+:[^/@\s]+@",
    re.IGNORECASE,
)
_KNOWN_TOKEN_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{16,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,})\b"
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+(?![A-Za-z0-9.-])"
)
_PHONE_RE = re.compile(
    r"(?<![\w])(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?|\d{2,4}[ .-])"
    r"\d{3,4}[ .-]\d{3,4}(?![\w])"
)
_SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")


def fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def scrub_secret_text(value: str) -> str:
    """Remove common credentials and high-confidence PII forms.

    The historical name remains for API compatibility. Free-form names are not guessed
    as PII; callers that know a value is private must additionally classify the whole
    payload as sensitive/secret so persistence keeps only a projection.
    """

    text = str(value)
    text = _PRIVATE_KEY_RE.sub(_REDACTED, text)
    text = _CREDENTIAL_URL_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}@", text)
    text = _SENSITIVE_QUERY_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}", text)
    text = _BEARER_RE.sub(lambda match: f"{match.group(1)} {_REDACTED}", text)
    text = _SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}",
        text,
    )
    text = _KNOWN_TOKEN_RE.sub(_REDACTED, text)
    text = _EMAIL_RE.sub(_REDACTED, text)
    text = _PHONE_RE.sub(_REDACTED, text)
    return _SSN_RE.sub(_REDACTED, text)


def _secret_field_name(value: Any) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", str(value).lower())
    return any(compact.endswith(suffix) for suffix in _SECRET_FIELD_SUFFIXES)


def scrub_secrets(value: Any, *, _depth: int = 0) -> Any:
    """Recursively scrub credentials/high-confidence PII at trust boundaries.

    Key-name filtering catches structured secrets; text filtering catches credentials
    embedded in URLs, headers, errors and summaries. Unknown objects are deep-copied
    rather than stringified so this helper does not silently change normal semantics.
    """

    if _depth > 32:
        return "[REDACTED:MAX_DEPTH]"
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            if _secret_field_name(key):
                result[deepcopy(key)] = _REDACTED
            else:
                result[deepcopy(key)] = scrub_secrets(item, _depth=_depth + 1)
        return result
    if isinstance(value, list):
        return [scrub_secrets(item, _depth=_depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_secrets(item, _depth=_depth + 1) for item in value)
    if isinstance(value, set):
        return {scrub_secrets(item, _depth=_depth + 1) for item in value}
    if isinstance(value, str):
        return scrub_secret_text(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "[REDACTED:BINARY]"
    return deepcopy(value)


def safe_action_descriptor(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"name": str(value)[:128], "arguments_fingerprint": fingerprint({})}
    name = str(value.get("name", ""))[:128]
    if "args" in value:
        arguments_value = value.get("args")
        args: dict[Any, Any] = (
            arguments_value if isinstance(arguments_value, dict) else {}
        )
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
        result["summary"] = scrub_secret_text(str(result["summary"]))[:1000]
    if "source_ref" in result:
        result["source_ref"] = scrub_secret_text(str(result["source_ref"]))[:2000]
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
        "error": scrub_secret_text(str(value["error"]))[:1000] if value.get("error") else None,
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
        return scrub_secret_text(str(value))
    redacted = redact_action_record(item)
    return json.dumps(redacted, ensure_ascii=False, sort_keys=True)
