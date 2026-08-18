from __future__ import annotations

from copy import deepcopy
from typing import Any


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
        provenance = item.get("provenance")
        if isinstance(provenance, dict):
            item["provenance"] = {
                str(key): deepcopy(val)
                for key, val in provenance.items()
                if str(key).lower() not in {"payload", "content", "body", "raw", "secret", "token"}
            }
        result.append(item)
    return result


def _resource_ecology_metadata(value: Any) -> dict[str, Any]:
    """Project resource ecology without raw evidence, notes or external responses."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {
        "bottleneck": deepcopy(value.get("bottleneck")),
        "exact_bottleneck_candidate_count": int(
            value.get("exact_bottleneck_candidate_count", 0) or 0
        ),
        "epistemic_rule": str(value.get("epistemic_rule", ""))[:2000],
    }
    candidates: list[dict[str, Any]] = []
    for raw in list(value.get("candidates") or [])[:16]:
        if not isinstance(raw, dict):
            continue
        opportunity = raw.get("opportunity") if isinstance(raw.get("opportunity"), dict) else {}
        profile = raw.get("resource_profile") if isinstance(raw.get("resource_profile"), dict) else {}
        work_items = []
        for work in list(raw.get("work_items") or [])[:8]:
            if not isinstance(work, dict):
                continue
            work_items.append(
                {
                    key: deepcopy(work.get(key))
                    for key in (
                        "id",
                        "opportunity_id",
                        "status",
                        "objective",
                        "estimated_gpu_hours",
                        "artifact_path",
                        "submission_observation_id",
                        "resource_event_id",
                    )
                    if key in work
                }
            )
        candidates.append(
            {
                "opportunity": {
                    key: deepcopy(opportunity.get(key))
                    for key in (
                        "id",
                        "title",
                        "kind",
                        "source_url",
                        "estimated_value",
                        "estimated_cost_value",
                        "unit",
                        "probability",
                        "estimated_gpu_hours",
                        "status",
                        "expires_at",
                        "expected_net_value",
                        "value_per_gpu_hour",
                    )
                    if key in opportunity
                },
                "resource_profile": {
                    key: deepcopy(profile.get(key))
                    for key in (
                        "opportunity_id",
                        "target_asset",
                        "target_unit",
                        "target_amount",
                        "eligibility_confidence",
                        "evidence_quality",
                        "blockers",
                        "qualification_score",
                        "epistemic_status",
                    )
                    if key in profile
                },
                "bottleneck_match": bool(raw.get("bottleneck_match")),
                "expected_resource_amount": raw.get("expected_resource_amount"),
                "expected_resource_per_gpu_hour": raw.get("expected_resource_per_gpu_hour"),
                "expected_runway_gain_days": raw.get("expected_runway_gain_days"),
                "work_items": work_items,
            }
        )
    result["candidates"] = candidates
    active_work = []
    for work in list(value.get("active_work") or [])[:16]:
        if not isinstance(work, dict):
            continue
        active_work.append(
            {
                key: deepcopy(work.get(key))
                for key in (
                    "id",
                    "opportunity_id",
                    "status",
                    "objective",
                    "estimated_gpu_hours",
                    "artifact_path",
                    "submission_observation_id",
                    "resource_event_id",
                )
                if key in work
            }
        )
    result["active_work"] = active_work
    result["unprofiled_opportunity_count"] = len(value.get("unprofiled_opportunities") or [])
    return result


def _work_port_metadata(value: Any) -> dict[str, Any]:
    """Remove remote references/fingerprints while preserving actionable port state."""
    if not isinstance(value, dict):
        return {}
    ports: dict[str, Any] = {}
    for name, raw in dict(value.get("ports") or {}).items():
        if not isinstance(raw, dict):
            continue
        ports[str(name)[:128]] = {
            key: deepcopy(raw.get(key))
            for key in ("server", "submit_tool", "outcome_tool")
            if key in raw
        }
    active = []
    for raw in list(value.get("active_submissions") or [])[:32]:
        if not isinstance(raw, dict):
            continue
        active.append(
            {
                key: deepcopy(raw.get(key))
                for key in (
                    "id",
                    "work_item_id",
                    "port_name",
                    "submitted_at",
                    "updated_at",
                    "submission_observation_id",
                    "remote_status",
                    "last_outcome_observation_id",
                )
                if key in raw
            }
        )
    return {
        "enabled": bool(value.get("enabled", False)),
        "readiness": deepcopy(value.get("readiness")),
        "ports": ports,
        "active_submissions": active,
    }


def provider_context(context: dict[str, Any]) -> dict[str, Any]:
    """Return the only runtime-context view allowed to leave local trust boundary."""

    public: dict[str, Any] = {}
    for key, value in context.items():
        name = str(key)
        if name.startswith("_"):
            continue
        if name == "sensorium":
            public[name] = _sensor_metadata(value)
            continue
        if name == "resource_ecology":
            public[name] = _resource_ecology_metadata(value)
            continue
        if name == "work_ports":
            public[name] = _work_port_metadata(value)
            continue
        public[name] = deepcopy(value)
    return public
