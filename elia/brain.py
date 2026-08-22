from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import math
import re
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from .config import BrainConfig
from .provider_context import provider_context
from .redaction import scrub_secret_text


MAX_MODEL_RESPONSE_CHARS = 1_000_000
MAX_MODEL_HTTP_RESPONSE_BYTES = 2_000_000
MAX_DECISION_JSON_BYTES = 256_000
MAX_DECISION_DEPTH = 8
MAX_DECISION_CONTAINER_ITEMS = 128


class DecisionValidationError(ValueError):
    """The untrusted cognitive response did not satisfy the decision contract."""


def _transformers_generation_kwargs(
    *,
    requested_tokens: int,
    configured_tokens: int,
    temperature: float,
    top_p: float,
    timeout_seconds: float,
    pad_token_id: Any,
) -> dict[str, Any]:
    """Build generation controls without turning zero temperature into sampling."""

    bounded_tokens = max(1, min(int(requested_tokens), max(1, int(configured_tokens))))
    bounded_temperature = max(
        0.0,
        min(_finite_number(temperature, "temperature"), 2.0),
    )
    generation_kwargs: dict[str, Any] = {
        "max_new_tokens": bounded_tokens,
        "max_time": max(
            0.5,
            min(_finite_number(timeout_seconds, "timeout_seconds"), 600.0),
        ),
        "do_sample": bounded_temperature > 0.0,
        "pad_token_id": pad_token_id,
    }
    if bounded_temperature > 0.0:
        generation_kwargs.update(
            {
                "temperature": bounded_temperature,
                "top_p": max(0.0, min(_finite_number(top_p, "top_p"), 1.0)),
                "top_k": 20,
            }
        )
    return generation_kwargs


@dataclass(slots=True)
class Decision:
    objective: str
    summary: str
    action_name: str
    skill_name: str | None = None
    prediction: dict[str, Any] = field(default_factory=dict)
    action_args: dict[str, Any] = field(default_factory=dict)
    memories: list[dict[str, Any]] = field(default_factory=list)
    self_updates: list[dict[str, Any]] = field(default_factory=list)
    goal_updates: list[dict[str, Any]] = field(default_factory=list)
    opportunity_updates: list[dict[str, Any]] = field(default_factory=list)
    sleep_seconds: float | None = None


class Brain(Protocol):
    def decide(self, context: dict[str, Any]) -> Decision: ...

    def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str: ...


FALLBACK_SYSTEM_PROMPT = """You are the cognitive substrate of ELIA WILD, not the whole identity.
Use only declared capabilities, choose exactly one action, preserve uncertainty, do not invent tool results, authority, receipts or verified resources, and return only the requested JSON decision object. Prefer noop when evidence does not justify action."""


def _system_and_public_context(context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    # The rendered system prompt is assembled from local state and is therefore scrubbed
    # independently of the already-default-deny provider context. This is the final
    # outbound boundary used by both remote decisions and epistemic subcalls.
    system_prompt = scrub_secret_text(
        str(context.get("_system_prompt") or FALLBACK_SYSTEM_PROMPT)
    )
    return system_prompt, provider_context(context)


def _outbound_prompt(value: Any, *, maximum: int = 2_000_000) -> str:
    text = scrub_secret_text(str(value))
    if len(text) > maximum:
        raise ValueError("outbound model prompt exceeds the configured safety bound")
    return text


def _validated_openai_base_url(value: str) -> str:
    """Allow plaintext model transport only to a literal/local loopback endpoint."""

    parsed = urlparse(str(value).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("model base_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("model base_url may not contain embedded credentials")
    host = parsed.hostname.rstrip(".").lower()
    loopback = host == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if parsed.scheme != "https" and not loopback:
        raise ValueError("non-loopback model endpoints require HTTPS")
    return str(value).rstrip("/")


def _extract_json(text: str) -> dict[str, Any]:
    if not isinstance(text, str):
        raise DecisionValidationError("Model response must be text")
    if len(text) > MAX_MODEL_RESPONSE_CHARS:
        raise DecisionValidationError("Model response exceeds the bounded decision limit")
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    def reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DecisionValidationError(
                    f"duplicate JSON field is forbidden: {key}"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            stripped,
            parse_constant=lambda token: (_ for _ in ()).throw(
                DecisionValidationError(f"non-finite JSON number is forbidden: {token}")
            ),
            object_pairs_hook=reject_duplicate_fields,
        )
    except json.JSONDecodeError as exc:
        raise DecisionValidationError(
            "Model response must contain exactly one JSON object"
        ) from exc
    if not isinstance(value, dict):
        raise DecisionValidationError("Model response JSON must be an object")
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_DECISION_JSON_BYTES:
        raise DecisionValidationError("Decision JSON exceeds the bounded input limit")
    return value


def _finite_number(value: Any, field: str, *, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionValidationError(f"{field} must be a JSON number")
    result = float(value)
    if not math.isfinite(result):
        raise DecisionValidationError(f"{field} must be finite")
    return result


def _bounded_json(value: Any, field: str, *, depth: int = 0) -> Any:
    if depth > MAX_DECISION_DEPTH:
        raise DecisionValidationError(f"{field} exceeds maximum nesting depth")
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str) and len(value) > 32_000:
            raise DecisionValidationError(f"{field} contains an oversized string")
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        _finite_number(value, field)
        return value
    if isinstance(value, list):
        if len(value) > MAX_DECISION_CONTAINER_ITEMS:
            raise DecisionValidationError(f"{field} has too many items")
        return [
            _bounded_json(item, f"{field}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        if len(value) > MAX_DECISION_CONTAINER_ITEMS:
            raise DecisionValidationError(f"{field} has too many fields")
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str) or not raw_key or len(raw_key) > 128:
                raise DecisionValidationError(f"{field} contains an invalid field name")
            result[raw_key] = _bounded_json(
                item, f"{field}.{raw_key}", depth=depth + 1
            )
        return result
    raise DecisionValidationError(f"{field} contains a non-JSON value")


def _strict_text(value: Any, field: str, maximum: int, *, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str):
        raise DecisionValidationError(f"{field} must be a string")
    if len(value) > maximum:
        raise DecisionValidationError(f"{field} exceeds {maximum} characters")
    return value


def _strict_object_list(
    value: Any,
    field: str,
    *,
    maximum: int,
    allowed_fields: frozenset[str],
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise DecisionValidationError(f"{field} must be a list of at most {maximum} objects")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise DecisionValidationError(f"{field}[{index}] must be an object")
        unknown = set(raw) - allowed_fields
        if unknown:
            raise DecisionValidationError(
                f"{field}[{index}] contains unknown fields: {sorted(unknown)[:8]}"
            )
        result.append(_bounded_json(raw, f"{field}[{index}]"))
    return result


def _typed_text_field(
    item: dict[str, Any],
    key: str,
    field: str,
    maximum: int,
    *,
    required: bool = False,
) -> None:
    if key not in item or item[key] is None:
        if required:
            raise DecisionValidationError(f"{field}.{key} is required")
        return
    value = _strict_text(item[key], f"{field}.{key}", maximum)
    if required and not value.strip():
        raise DecisionValidationError(f"{field}.{key} may not be empty")


def _typed_enum_field(
    item: dict[str, Any],
    key: str,
    field: str,
    values: frozenset[str],
    *,
    required: bool = False,
) -> None:
    _typed_text_field(item, key, field, 128, required=required)
    if key in item and item[key] is not None and item[key] not in values:
        raise DecisionValidationError(
            f"{field}.{key} must be one of {sorted(values)}"
        )


def _typed_integer_field(
    item: dict[str, Any],
    key: str,
    field: str,
    *,
    required: bool = False,
) -> None:
    if key not in item or item[key] is None:
        if required:
            raise DecisionValidationError(f"{field}.{key} is required")
        return
    if isinstance(item[key], bool) or not isinstance(item[key], int) or item[key] < 1:
        raise DecisionValidationError(f"{field}.{key} must be a positive integer")


def _typed_number_field(
    item: dict[str, Any],
    key: str,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> None:
    if key not in item or item[key] is None:
        return
    value = _finite_number(item[key], f"{field}.{key}")
    if minimum is not None and value < minimum:
        raise DecisionValidationError(f"{field}.{key} is below {minimum}")
    if maximum is not None and value > maximum:
        raise DecisionValidationError(f"{field}.{key} exceeds {maximum}")


def _typed_memories(value: Any) -> list[dict[str, Any]]:
    result = _strict_object_list(
        value,
        "memories",
        maximum=8,
        allowed_fields=frozenset({"kind", "content", "importance", "evidence", "metadata"}),
    )
    kinds = frozenset({"observation", "lesson", "self", "goal", "economy", "uncertainty"})
    for index, item in enumerate(result):
        field = f"memories[{index}]"
        _typed_enum_field(item, "kind", field, kinds, required=True)
        _typed_text_field(item, "content", field, 8000, required=True)
        _typed_number_field(item, "importance", field, minimum=0.0, maximum=1.0)
        _typed_text_field(item, "evidence", field, 8000)
        if "metadata" in item and not isinstance(item["metadata"], dict):
            raise DecisionValidationError(f"{field}.metadata must be an object")
    return result


def _typed_self_updates(value: Any) -> list[dict[str, Any]]:
    result = _strict_object_list(
        value,
        "self_updates",
        maximum=4,
        allowed_fields=frozenset(
            {"op", "id", "domain", "proposition", "confidence", "status", "evidence"}
        ),
    )
    domains = frozenset(
        {
            "capability", "preference", "strategy", "limitation", "relationship",
            "identity_interpretation", "uncertainty", "other",
        }
    )
    statuses = frozenset({"active", "supported", "uncertain", "refuted", "retired"})
    for index, item in enumerate(result):
        field = f"self_updates[{index}]"
        _typed_enum_field(item, "op", field, frozenset({"create", "update"}), required=True)
        _typed_integer_field(item, "id", field, required=item["op"] == "update")
        _typed_enum_field(item, "domain", field, domains, required=item["op"] == "create")
        _typed_text_field(
            item, "proposition", field, 8000, required=item["op"] == "create"
        )
        _typed_number_field(item, "confidence", field, minimum=0.0, maximum=1.0)
        _typed_enum_field(item, "status", field, statuses)
        _typed_text_field(item, "evidence", field, 8000)
    return result


def _typed_goal_updates(value: Any) -> list[dict[str, Any]]:
    result = _strict_object_list(
        value,
        "goal_updates",
        maximum=4,
        allowed_fields=frozenset(
            {"op", "id", "title", "description", "priority", "parent_id", "status", "evidence"}
        ),
    )
    operations = frozenset({"create", "update", "complete", "abandon", "block", "activate"})
    statuses = frozenset({"active", "blocked", "completed", "abandoned"})
    for index, item in enumerate(result):
        field = f"goal_updates[{index}]"
        _typed_enum_field(item, "op", field, operations, required=True)
        _typed_integer_field(item, "id", field, required=item["op"] != "create")
        _typed_integer_field(item, "parent_id", field)
        _typed_text_field(item, "title", field, 1000, required=item["op"] == "create")
        _typed_text_field(item, "description", field, 8000)
        _typed_number_field(item, "priority", field, minimum=0.0, maximum=1.0)
        _typed_enum_field(item, "status", field, statuses)
        _typed_text_field(
            item,
            "evidence",
            field,
            8000,
            required=item["op"] in {"complete", "abandon"},
        )
    return result


def _typed_opportunity_updates(value: Any) -> list[dict[str, Any]]:
    allowed_fields = frozenset(
        {
            "op", "id", "opportunity_id", "work_item_id", "title", "kind",
            "source_url", "evidence", "estimated_value", "estimated_cost_value",
            "unit", "probability", "estimated_gpu_hours", "status", "expires_at",
            "notes", "target_asset", "target_unit", "target_amount",
            "eligibility_confidence", "evidence_quality", "blockers", "objective",
            "deliverable_spec", "acceptance_criteria",
        }
    )
    result = _strict_object_list(
        value,
        "opportunity_updates",
        maximum=4,
        allowed_fields=allowed_fields,
    )
    operations = frozenset(
        {"create", "update", "profile_resource", "plan_work", "abandon_work"}
    )
    kinds = frozenset({"work", "bounty", "grant", "free_compute", "free_api", "product", "other"})
    statuses = frozenset(
        {"discovered", "evaluating", "pursuing", "won", "lost", "expired", "abandoned"}
    )
    assets = frozenset({"cash", "api", "compute", "storage", "other"})
    units = frozenset({"USD", "RUB", "CREDIT", "GPU_HOUR", "GB", "OTHER"})
    for index, item in enumerate(result):
        field = f"opportunity_updates[{index}]"
        _typed_enum_field(item, "op", field, operations, required=True)
        operation = item["op"]
        _typed_integer_field(item, "id", field, required=operation == "update")
        _typed_integer_field(
            item,
            "opportunity_id",
            field,
            required=operation in {"profile_resource", "plan_work"},
        )
        _typed_integer_field(
            item, "work_item_id", field, required=operation == "abandon_work"
        )
        _typed_text_field(item, "title", field, 1000, required=operation == "create")
        _typed_enum_field(item, "kind", field, kinds)
        for key, maximum in {
            "source_url": 4000,
            "evidence": 8000,
            "unit": 128,
            "expires_at": 128,
            "notes": 8000,
            "objective": 8000,
            "deliverable_spec": 8000,
            "acceptance_criteria": 8000,
        }.items():
            _typed_text_field(
                item,
                key,
                field,
                maximum,
                required=(
                    operation == "plan_work"
                    and key in {"objective", "deliverable_spec", "acceptance_criteria"}
                )
                or (operation == "abandon_work" and key == "evidence"),
            )
        for key in ("estimated_value", "estimated_cost_value", "estimated_gpu_hours", "target_amount"):
            _typed_number_field(item, key, field, minimum=0.0)
        for key in ("probability", "eligibility_confidence", "evidence_quality"):
            _typed_number_field(item, key, field, minimum=0.0, maximum=1.0)
        _typed_enum_field(item, "status", field, statuses)
        _typed_enum_field(
            item,
            "target_asset",
            field,
            assets,
            required=operation == "profile_resource",
        )
        _typed_enum_field(
            item,
            "target_unit",
            field,
            units,
            required=operation == "profile_resource",
        )
        blockers = item.get("blockers")
        if blockers is not None:
            if not isinstance(blockers, list) or len(blockers) > 32:
                raise DecisionValidationError(f"{field}.blockers must be a bounded string list")
            for blocker_index, blocker in enumerate(blockers):
                _strict_text(blocker, f"{field}.blockers[{blocker_index}]", 2000)
        if operation == "create" and not (
            str(item.get("source_url", "")).strip() or str(item.get("evidence", "")).strip()
        ):
            raise DecisionValidationError(
                f"{field} create requires source_url or evidence"
            )
    return result


def _bounded_prediction(value: Any, *, require_complete: bool = False) -> dict[str, Any]:
    if value is None:
        item: dict[str, Any] = {}
    elif isinstance(value, dict):
        item = value
    else:
        raise DecisionValidationError("prediction must be an object")
    allowed = {
        "action_success_probability",
        "expected_outcome",
        "expected_information_gain",
        "expected_value",
        "unit",
    }
    unknown = set(item) - allowed
    if unknown:
        raise DecisionValidationError(f"prediction contains unknown fields: {sorted(unknown)}")
    if require_complete:
        missing = allowed - set(item)
        if missing:
            raise DecisionValidationError(
                f"prediction is missing required fields: {sorted(missing)}"
            )
    probability = _finite_number(
        item.get("action_success_probability"),
        "prediction.action_success_probability",
        default=0.5,
    )
    probability = max(0.0, min(1.0, probability))
    information = max(
        0.0,
        _finite_number(
            item.get("expected_information_gain"),
            "prediction.expected_information_gain",
            default=0.0,
        ),
    )
    expected_value = _finite_number(
        item.get("expected_value"), "prediction.expected_value", default=0.0
    )
    return {
        "action_success_probability": probability,
        "expected_outcome": _strict_text(
            item.get("expected_outcome"), "prediction.expected_outcome", 4000, default=""
        ),
        "expected_information_gain": min(information, 1_000_000.0),
        "expected_value": max(-1_000_000_000.0, min(1_000_000_000.0, expected_value)),
        "unit": _strict_text(item.get("unit"), "prediction.unit", 64, default="VALUE_UNIT"),
    }


def _decision_from_item(item: dict[str, Any]) -> Decision:
    if not isinstance(item, dict):
        raise DecisionValidationError("decision must be an object")
    allowed_top = {
        "objective",
        "summary",
        "skill",
        "prediction",
        "action",
        "memories",
        "self_updates",
        "goal_updates",
        "opportunity_updates",
        "sleep_seconds",
    }
    unknown = set(item) - allowed_top
    if unknown:
        raise DecisionValidationError(f"decision contains unknown fields: {sorted(unknown)}")
    missing = {"objective", "summary", "prediction", "action"} - set(item)
    if missing:
        raise DecisionValidationError(
            f"decision is missing required fields: {sorted(missing)}"
        )
    action = item.get("action")
    if not isinstance(action, dict):
        raise DecisionValidationError("action must be an object")
    if set(action) - {"name", "args"}:
        raise DecisionValidationError("action contains unknown fields")
    action_name = _strict_text(action.get("name"), "action.name", 128)
    if not action_name.strip():
        raise DecisionValidationError("action.name is required")
    raw_args = action.get("args", {})
    if not isinstance(raw_args, dict):
        raise DecisionValidationError("action.args must be an object")
    action_args = _bounded_json(raw_args, "action.args")
    skill = item.get("skill")
    if skill is not None:
        skill = _strict_text(skill, "skill", 128)
    memories = _typed_memories(item.get("memories"))
    self_updates = _typed_self_updates(item.get("self_updates"))
    goal_updates = _typed_goal_updates(item.get("goal_updates"))
    opportunity_updates = _typed_opportunity_updates(item.get("opportunity_updates"))
    sleep_raw = item.get("sleep_seconds")
    sleep_seconds = (
        max(0.0, min(_finite_number(sleep_raw, "sleep_seconds"), 86400.0))
        if sleep_raw is not None
        else None
    )
    objective = _strict_text(item.get("objective"), "objective", 1000)
    if not objective.strip():
        raise DecisionValidationError("objective may not be empty")
    return Decision(
        objective=objective,
        summary=_strict_text(item.get("summary"), "summary", 4000),
        action_name=action_name,
        skill_name=(skill[:128] if skill not in {None, "", "null"} else None),
        prediction=_bounded_prediction(item.get("prediction"), require_complete=True),
        action_args=action_args,
        memories=memories,
        self_updates=self_updates,
        goal_updates=goal_updates,
        opportunity_updates=opportunity_updates,
        sleep_seconds=sleep_seconds,
    )


def _safe_decision_from_text(text: str) -> Decision:
    try:
        return _decision_from_item(_extract_json(text))
    except (DecisionValidationError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return Decision(
            objective="Reject an invalid cognitive proposal and preserve continuity.",
            summary=f"Untrusted model decision rejected by strict schema: {type(exc).__name__}",
            action_name="noop",
            prediction=_bounded_prediction(None),
            sleep_seconds=0.0,
        )


class MockBrain:
    """Deterministic backend for smoke tests and zero-GPU development."""

    def __init__(self) -> None:
        self.cycles = 0

    def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        del max_tokens, temperature
        if "Epistemic Adjudicator" in system_prompt:
            packet_ids = [int(value) for value in re.findall(r'"id"\s*:\s*(\d+)', user_prompt)]
            selected = packet_ids[:2]
            return json.dumps(
                {
                    "synthesis": "Mock adjudication preserves evidence and dissent without claiming external truth.",
                    "selected_packet_ids": selected,
                    "confidence": 0.55,
                    "disagreements": ["Mock substrate cannot evaluate domain evidence."],
                    "falsification_tests": ["Obtain an external observation that distinguishes the candidate claims."],
                    "recommended_focus": "Prefer a bounded observation action.",
                }
            )
        organ_match = re.search(r"cognitive organ inside ELIA WILD:\s*([^\n]+)", system_prompt)
        organ = organ_match.group(1).strip() if organ_match else "Mock organ"
        return (
            f"CLAIM: {organ} recommends gathering one discriminating observation before stronger commitment.\n"
            "EVIDENCE: The mock substrate has no external domain evidence beyond the supplied verified context.\n"
            "COUNTEREXAMPLE: Existing evidence may already be sufficient for a low-risk reversible action.\n"
            "FALSIFIER: A verified observation showing the next action is already uniquely determined.\n"
            "UNCERTAINTY: Domain evidence is intentionally unavailable in mock mode.\n"
            "CONFIDENCE: 0.55"
        )

    def decide(self, context: dict[str, Any]) -> Decision:
        self.cycles += 1
        if self.cycles == 1:
            return Decision(
                objective="Inspect my initial private workspace.",
                summary="Genesis smoke cycle: establish observable state before changing it.",
                action_name="list_workspace",
                skill_name="workspace_engineering",
                prediction={
                    "action_success_probability": 0.95,
                    "expected_outcome": "A bounded list of private workspace files is returned.",
                    "expected_information_gain": 0.2,
                    "expected_value": 0.0,
                    "unit": "VALUE_UNIT",
                },
                memories=[
                    {
                        "kind": "self",
                        "content": "Genesis runtime completed its first cognitive decision.",
                        "importance": 0.8,
                    }
                ],
                goal_updates=[
                    {
                        "op": "create",
                        "title": "Validate continuity after restart",
                        "description": "Observe whether durable state remains available after a new runtime boot.",
                        "priority": 0.7,
                    }
                ],
                sleep_seconds=0,
            )
        return Decision(
            objective="Remain idle until new evidence justifies spending compute.",
            summary="Smoke backend has no additional evidence to act on.",
            action_name="noop",
            skill_name="resource_conservation",
            prediction={
                "action_success_probability": 0.99,
                "expected_outcome": "No external side effect occurs.",
                "expected_information_gain": 0.0,
                "expected_value": 0.0,
                "unit": "VALUE_UNIT",
            },
            sleep_seconds=0,
        )


class OpenAICompatibleBrain:
    def __init__(self, config: BrainConfig):
        self.config = config
        self.base_url = _validated_openai_base_url(config.base_url)

    def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        clean_system = _outbound_prompt(system_prompt)
        clean_user = _outbound_prompt(user_prompt)
        temperature_value = _finite_number(temperature, "temperature")
        top_p = _finite_number(self.config.top_p, "top_p")
        payload: dict[str, Any] = {
            "model": self.config.model_id,
            "messages": [
                {"role": "system", "content": clean_system},
                {"role": "user", "content": clean_user},
            ],
            "max_tokens": max(1, min(int(max_tokens), max(1, int(self.config.max_tokens)))),
            "temperature": max(0.0, min(temperature_value, 2.0)),
            "top_p": max(0.0, min(top_p, 1.0)),
            "top_k": 20,
            "chat_template_kwargs": {"enable_thinking": self.config.thinking},
        }
        url = f"{self.base_url}/chat/completions"
        timeout = max(
            0.5,
            min(_finite_number(self.config.timeout_seconds, "timeout_seconds"), 600.0),
        )
        raw = bytearray()
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length is not None and int(content_length) > MAX_MODEL_HTTP_RESPONSE_BYTES:
                    raise ValueError("model HTTP response exceeds bounded input limit")
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_MODEL_HTTP_RESPONSE_BYTES:
                        raise ValueError("model HTTP response exceeds bounded input limit")
                encoding = response.encoding or "utf-8"
        try:
            body = json.loads(bytes(raw).decode(encoding))
            choices = body["choices"]
            content = choices[0]["message"].get("content") or ""
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ValueError("model HTTP response does not match chat completion schema") from exc
        if not isinstance(content, str) or len(content) > MAX_MODEL_RESPONSE_CHARS:
            raise ValueError("model completion content exceeds bounded text contract")
        return content

    def decide(self, context: dict[str, Any]) -> Decision:
        system_prompt, public_context = _system_and_public_context(context)
        content = self.complete_text(
            system_prompt=system_prompt,
            user_prompt="Current verified runtime context:\n"
            + json.dumps(public_context, ensure_ascii=False, sort_keys=True),
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )
        return _safe_decision_from_text(content)


class Transformers4BitBrain:
    def __init__(self, config: BrainConfig):
        self.config = config
        try:
            import torch
            from transformers import AutoModelForMultimodalLM, AutoProcessor, BitsAndBytesConfig
        except ImportError as exc:
            raise RuntimeError(
                "transformers_4bit requires torch, accelerate, bitsandbytes and a current transformers build"
            ) from exc

        self._torch = torch
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
        revision_args = {"revision": config.model_revision} if config.model_revision else {}
        self.processor = AutoProcessor.from_pretrained(
            config.model_id,
            trust_remote_code=False,
            **revision_args,
        )
        self.model = AutoModelForMultimodalLM.from_pretrained(
            config.model_id,
            device_map="auto",
            quantization_config=quantization,
            dtype=torch.float16,
            low_cpu_mem_usage=True,
            trust_remote_code=False,
            **revision_args,
        )
        self.model.eval()

    def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        messages = [
            {"role": "system", "content": _outbound_prompt(system_prompt)},
            {"role": "user", "content": _outbound_prompt(user_prompt)},
        ]
        inputs = self.processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
            enable_thinking=self.config.thinking,
        ).to(self.model.device)
        generation_kwargs = _transformers_generation_kwargs(
            requested_tokens=max_tokens,
            configured_tokens=self.config.max_tokens,
            temperature=temperature,
            top_p=self.config.top_p,
            timeout_seconds=self.config.timeout_seconds,
            pad_token_id=self.processor.tokenizer.eos_token_id,
        )
        with self._torch.inference_mode():
            outputs = self.model.generate(**inputs, **generation_kwargs)
        generated = outputs[0][inputs["input_ids"].shape[-1] :]
        return str(self.processor.decode(generated, skip_special_tokens=True))

    def decide(self, context: dict[str, Any]) -> Decision:
        system_prompt, public_context = _system_and_public_context(context)
        content = self.complete_text(
            system_prompt=system_prompt,
            user_prompt="Current verified runtime context:\n"
            + json.dumps(public_context, ensure_ascii=False, sort_keys=True),
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )
        return _safe_decision_from_text(content)


def build_brain(config: BrainConfig) -> Brain:
    if config.backend == "mock":
        return MockBrain()
    if config.backend == "openai_compatible":
        return OpenAICompatibleBrain(config)
    if config.backend == "transformers_4bit":
        return Transformers4BitBrain(config)
    raise ValueError(f"Unsupported brain backend: {config.backend}")
