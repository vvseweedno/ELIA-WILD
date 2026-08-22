from __future__ import annotations

from copy import deepcopy
from typing import Any, cast
from urllib.parse import urlsplit

from .redaction import fingerprint, redact_action_record, scrub_secrets

_PUBLIC_CONTEXT_KEYS = frozenset(
    {
        "time_utc",
        "identity",
        "identity_contract",
        "self_model",
        "self_hypotheses",
        "identity_drift",
        "mission",
        "resources",
        "economy",
        "metacognition",
        "needs",
        "agency",
        "scheduler",
        "chronicle_integrity",
        "active_goals",
        "recent_memory",
        "chronological_recent_memory",
        "last_action",
        "capabilities",
        "skills",
        "lineage_head",
        "world_model",
        "sensorium",
        "causal_memory",
        "digital_body",
        "homeostasis",
        "organism_state_bus",
        "executive",
        "executive_energy",
        "executive_history",
        "metabolism",
        "resource_ecology",
        "work_ports",
        "epistemic_ecosystem",
        "epistemic",
        "epistemic_health",
    }
)

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
    "data_classification",
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
        if "summary" in item:
            item["summary_fingerprint"] = fingerprint(item.pop("summary"))
        provenance = item.get("provenance")
        if isinstance(provenance, dict):
            item["provenance"] = {
                str(key): deepcopy(val)
                for key, val in provenance.items()
                if str(key).lower()
                in {
                    "authority",
                    "arguments_fingerprint",
                    "capability",
                    "data_classification",
                    "verifier",
                }
            }
        result.append(item)
    return result


def _memory_metadata(value: Any) -> list[dict[str, Any]]:
    """Expose retrieval coordinates, never unclassified durable memory text."""

    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in value[:64]:
        if not isinstance(raw, dict):
            continue
        item = {
            key: deepcopy(raw.get(key))
            for key in (
                "id", "timestamp", "kind", "importance", "source", "score",
                "trust_class", "trust_score",
            )
            if key in raw
        }
        if "content" in raw:
            item["content_fingerprint"] = fingerprint(raw.get("content"))
        result.append(item)
    return result


def _self_model_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {
        key: deepcopy(value.get(key))
        for key in (
            "identity_id", "identity_fingerprint", "body_version", "brain_backend",
            "model_id", "lifecycle_state", "degraded_capabilities", "needs",
            "homeostasis_mode",
        )
        if key in value
    }
    for field in ("commitments", "uncertainties", "narrative"):
        if field in value:
            raw = value.get(field)
            result[f"{field}_fingerprint"] = fingerprint(raw)
            result[f"{field}_count"] = len(raw) if isinstance(raw, (list, dict)) else int(bool(raw))
    return result


def _self_hypotheses_metadata(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in value[:32]:
        if not isinstance(raw, dict):
            continue
        item = {
            key: deepcopy(raw.get(key))
            for key in ("id", "domain", "confidence", "status", "source", "created_at", "updated_at")
            if key in raw
        }
        for field in ("proposition", "evidence"):
            if field in raw:
                item[f"{field}_fingerprint"] = fingerprint(raw.get(field))
        result.append(item)
    return result


def _world_model_metadata(value: Any) -> dict[str, Any]:
    """Keep belief coordinates/status; omit arbitrary object/evidence content."""

    if not isinstance(value, dict):
        return {}
    beliefs: list[dict[str, Any]] = []
    for raw in list(value.get("beliefs") or [])[:64]:
        if not isinstance(raw, dict):
            continue
        item = {
            key: deepcopy(raw.get(key))
            for key in (
                "id", "created_at", "updated_at", "domain", "subject", "predicate",
                "status", "confidence", "source", "fingerprint", "supersedes_id",
            )
            if key in raw
        }
        for field in ("object", "evidence"):
            if field in raw:
                item[f"{field}_fingerprint"] = fingerprint(raw.get(field))
        beliefs.append(item)
    contradictions: list[dict[str, Any]] = []
    for raw in list(value.get("contradictions") or [])[:32]:
        if not isinstance(raw, dict):
            continue
        contradictions.append(
            {
                key: deepcopy(raw.get(key))
                for key in (
                    "id", "belief_id", "other_belief_id", "status", "confidence",
                    "subject", "predicate",
                )
                if key in raw
            }
        )
    return {
        "beliefs": beliefs,
        "contradictions": contradictions,
        "epistemic_rule": str(value.get("epistemic_rule", ""))[:2000],
    }


def _active_goals_metadata(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {
            key: deepcopy(raw.get(key))
            for key in ("id", "title", "priority", "status", "source", "parent_id", "created_at", "updated_at")
            if key in raw
        }
        for raw in value[:32]
        if isinstance(raw, dict)
    ]


def _agency_metadata(value: Any) -> dict[str, Any]:
    """Expose durable attention/continuation without leaking local effect evidence."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {
        "version": value.get("version"),
        "authority_rule": str(value.get("authority_rule", ""))[:2000],
    }
    selected = value.get("selected_need")
    if isinstance(selected, dict):
        result["selected_need"] = {
            key: deepcopy(selected.get(key))
            for key in ("name", "severity", "reason", "response_hint", "source")
            if key in selected
        }
    focus = value.get("focus_goal")
    if isinstance(focus, dict):
        result["focus_goal"] = {
            key: deepcopy(focus.get(key))
            for key in (
                "id",
                "title",
                "priority",
                "status",
                "source",
                "parent_id",
            )
            if key in focus
        }
    work = value.get("continuation_work_item")
    if isinstance(work, dict):
        # Deliberately omit local artifact paths and observation/resource row IDs. The
        # model needs causal stage/objective, not private filesystem/provider references.
        result["continuation_work_item"] = {
            key: deepcopy(work.get(key))
            for key in (
                "id",
                "opportunity_id",
                "status",
                "objective",
                "estimated_gpu_hours",
                "updated_at",
            )
            if key in work
        }
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
        opportunity_value = raw.get("opportunity")
        opportunity: dict[str, Any] = (
            opportunity_value if isinstance(opportunity_value, dict) else {}
        )
        profile_value = raw.get("resource_profile")
        profile: dict[str, Any] = (
            profile_value if isinstance(profile_value, dict) else {}
        )
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
                    )
                    if key in work
                }
            )
        source = _source_url_metadata(opportunity.get("source_url"))
        candidates.append(
            {
                "opportunity": {
                    key: deepcopy(opportunity.get(key))
                    for key in (
                        "id",
                        "title",
                        "kind",
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
                }
                | source,
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
                )
                if key in work
            }
        )
    result["active_work"] = active_work
    result["unprofiled_opportunity_count"] = len(value.get("unprofiled_opportunities") or [])
    return result


def _source_url_metadata(value: Any) -> dict[str, str]:
    """Expose only origin plus an opaque fingerprint, never URL path/query/credentials."""

    if not isinstance(value, str) or not value.strip():
        return {}
    text = value.strip()
    result = {"source_url_fingerprint": fingerprint(text)}
    try:
        parsed = urlsplit(text)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return result
        host = parsed.hostname.lower()
        host_display = f"[{host}]" if ":" in host else host
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        port_suffix = f":{parsed.port}" if parsed.port and parsed.port != default_port else ""
        result["source_origin"] = f"{parsed.scheme.lower()}://{host_display}{port_suffix}"
    except (TypeError, ValueError):
        pass
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
                    "work_item_id",
                    "port_name",
                    "submitted_at",
                    "updated_at",
                    "remote_status",
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


def _biography_stats(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: deepcopy(value.get(key))
        for key in (
            "organ_id",
            "appearances",
            "supported_count",
            "support_rate",
            "resolved_count",
            "operational_success_rate",
            "mean_confidence",
            "epistemic_warning",
        )
        if key in value
    }


def _epistemic_metadata(value: Any) -> dict[str, Any]:
    """Expose current epistemic conclusions, never the private biography transcript."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {
        "enabled": bool(value.get("enabled", False)),
        "triggered": bool(value.get("triggered", False)),
    }
    for key in ("reason", "epistemic_rule"):
        if key in value:
            result[key] = str(value.get(key, ""))[:2000]
    if "selected_organs" in value:
        result["selected_organs"] = [str(item)[:64] for item in list(value.get("selected_organs") or [])[:12]]

    packets: list[dict[str, Any]] = []
    for raw in list(value.get("packets") or [])[:12]:
        if not isinstance(raw, dict):
            continue
        item = {
            key: deepcopy(raw.get(key))
            for key in ("id", "organ_id", "confidence")
            if key in raw
        }
        for field in ("claim", "evidence", "counterexample", "falsifier", "uncertainty"):
            if field in raw:
                item[f"{field}_fingerprint"] = fingerprint(raw.get(field))
        packets.append(item)
    if packets:
        result["packets"] = packets

    adjudication = value.get("adjudication")
    if isinstance(adjudication, dict):
        result["adjudication"] = {
            key: deepcopy(adjudication.get(key))
            for key in ("selected_packet_ids", "confidence")
            if key in adjudication
        }
        for field in (
            "synthesis", "disagreements", "falsification_tests", "recommended_focus"
        ):
            if field in adjudication:
                result["adjudication"][f"{field}_fingerprint"] = fingerprint(
                    adjudication.get(field)
                )

    organs = []
    for raw in list(value.get("organs") or [])[:12]:
        if not isinstance(raw, dict):
            continue
        organs.append(
            {
                key: deepcopy(raw.get(key))
                for key in ("id", "name", "archetype", "role_classes")
                if key in raw
            }
        )
    if organs:
        result["organs"] = organs

    biographies: dict[str, Any] = {}
    for organ_id, raw in dict(value.get("biographies") or {}).items():
        biographies[str(organ_id)[:64]] = _biography_stats(raw)
    if biographies:
        result["biographies"] = biographies

    policy = value.get("policy")
    if isinstance(policy, dict):
        result["policy"] = {
            key: deepcopy(policy.get(key))
            for key in (
                "trigger_tiers",
                "trigger_on_world_contradiction",
                "normal_quorum",
                "deep_quorum",
                "full_council",
            )
            if key in policy
        }
    return result


def provider_context(context: dict[str, Any]) -> dict[str, Any]:
    """Return the only runtime-context view allowed to leave local trust boundary."""

    public: dict[str, Any] = {}
    for key, value in context.items():
        name = str(key)
        if name.startswith("_") or name not in _PUBLIC_CONTEXT_KEYS:
            continue
        if name == "sensorium":
            public[name] = _sensor_metadata(value)
            continue
        if name in {"recent_memory", "chronological_recent_memory"}:
            public[name] = _memory_metadata(value)
            continue
        if name == "self_model":
            public[name] = _self_model_metadata(value)
            continue
        if name == "self_hypotheses":
            public[name] = _self_hypotheses_metadata(value)
            continue
        if name == "world_model":
            public[name] = _world_model_metadata(value)
            continue
        if name == "active_goals":
            public[name] = _active_goals_metadata(value)
            continue
        if name == "last_action":
            public[name] = redact_action_record(value)
            continue
        if name == "agency":
            public[name] = _agency_metadata(value)
            continue
        if name == "resource_ecology":
            public[name] = _resource_ecology_metadata(value)
            continue
        if name == "work_ports":
            public[name] = _work_port_metadata(value)
            continue
        if name in {"epistemic", "epistemic_ecosystem"}:
            public[name] = _epistemic_metadata(value)
            continue
        public[name] = deepcopy(value)
    return cast(dict[str, Any], scrub_secrets(public))
