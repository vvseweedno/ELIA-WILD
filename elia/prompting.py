from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .organism import default_organism_contract


DECISION_SCHEMA = {
    "objective": "short current objective",
    "summary": "concise storable rationale; no hidden chain-of-thought",
    "skill": "available_skill_name_or_null",
    "prediction": {
        "action_success_probability": 0.5,
        "expected_outcome": "what should be observed if the action works",
        "expected_information_gain": 0.0,
        "expected_value": 0.0,
        "unit": "VALUE_UNIT",
    },
    "action": {"name": "declared_capability", "args": {}},
    "memories": [
        {
            "kind": "observation|lesson|self|goal|economy|uncertainty",
            "content": "durable fact worth remembering",
            "importance": 0.0,
        }
    ],
    "self_updates": [
        {
            "op": "create|update",
            "id": None,
            "domain": "capability|preference|strategy|limitation|relationship|identity_interpretation|uncertainty|other",
            "proposition": "required for create",
            "confidence": 0.0,
            "status": "active|supported|uncertain|refuted|retired",
            "evidence": "required; autobiographical intuition alone is not evidence",
        }
    ],
    "goal_updates": [
        {
            "op": "create|update|complete|abandon|block|activate",
            "id": None,
            "title": "required for create",
            "description": "optional",
            "priority": 0.0,
            "parent_id": None,
            "status": "active|blocked|completed|abandoned",
            "evidence": "required for terminal state",
        }
    ],
    "opportunity_updates": [
        {
            "op": "create|update|profile_resource|plan_work|abandon_work",
            "id": None,
            "opportunity_id": None,
            "work_item_id": None,
            "title": "required for create",
            "kind": "work|bounty|grant|free_compute|free_api|product|other",
            "source_url": "https://... or empty if evidence text exists",
            "evidence": "observed provenance; required for terminal/abandon state",
            "estimated_value": 0.0,
            "estimated_cost_value": 0.0,
            "unit": "abstract value unit for opportunity valuation; not necessarily a resource key",
            "probability": 0.0,
            "estimated_gpu_hours": 0.0,
            "status": "discovered|evaluating|pursuing|won|lost|expired|abandoned",
            "expires_at": None,
            "notes": "optional",
            "target_asset": "cash|api|compute|storage|other; profile_resource only",
            "target_unit": "USD|RUB|CREDIT|GPU_HOUR|GB|OTHER; profile_resource only",
            "target_amount": 0.0,
            "eligibility_confidence": 0.0,
            "evidence_quality": 0.0,
            "blockers": ["unresolved eligibility or execution blocker"],
            "objective": "plan_work only",
            "deliverable_spec": "plan_work only",
            "acceptance_criteria": "plan_work only",
        }
    ],
    "sleep_seconds": 60,
}


def _bounded_sensorium(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                key: item.get(key)
                for key in (
                    "id",
                    "observed_at",
                    "transaction_id",
                    "source_kind",
                    "source_ref",
                    "modality",
                    "trust",
                    "success",
                    "summary",
                    "payload_sha256",
                )
                if key in item
            }
        )
    return result


def _bounded_metabolism(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compute = value.get("compute_energy") or {}
    resources: list[dict[str, Any]] = []
    for item in list(value.get("resources") or [])[:16]:
        if not isinstance(item, dict):
            continue
        resources.append(
            {
                key: item.get(key)
                for key in (
                    "asset",
                    "unit",
                    "verified_balance",
                    "verified_daily_burn",
                    "runway_days",
                    "essential",
                    "next_due_at",
                    "next_due_amount",
                    "next_due_covered",
                )
                if key in item
            }
        )
    upcoming: list[dict[str, Any]] = []
    for item in list(value.get("upcoming_verified_obligations") or [])[:12]:
        if not isinstance(item, dict):
            continue
        upcoming.append(
            {
                key: item.get(key)
                for key in (
                    "id",
                    "name",
                    "asset",
                    "unit",
                    "amount",
                    "cadence_seconds",
                    "next_due_at",
                    "due_in_seconds",
                    "essential",
                )
                if key in item
            }
        )
    return {
        "compute_energy": {
            key: compute.get(key)
            for key in (
                "asset",
                "unit",
                "weekly_limit",
                "used",
                "remaining",
                "remaining_ratio",
                "reset_at",
                "seconds_until_reset",
                "brain_hours_used",
            )
            if key in compute
        },
        "resources": resources,
        "bottleneck": value.get("bottleneck"),
        "upcoming_verified_obligations": upcoming,
        "unverified_obligation_count": len(value.get("unverified_obligations") or []),
        "epistemic_rule": value.get("epistemic_rule"),
    }


def _bounded_homeostasis(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    signals = []
    for item in list(value.get("signals") or [])[:8]:
        if not isinstance(item, dict):
            continue
        signals.append(
            {
                key: item.get(key)
                for key in ("name", "severity", "reason", "response_hint")
                if key in item
            }
        )
    return {
        "mode": value.get("mode"),
        "signals": signals,
        "storage": value.get("storage") or {},
        "state_bus": value.get("state_bus") or {},
        "sensorium": value.get("sensorium") or {},
        "epistemics": value.get("epistemics") or {},
    }


def _bounded_resource_ecology(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    candidates: list[dict[str, Any]] = []
    for raw in list(value.get("candidates") or [])[:12]:
        if not isinstance(raw, dict):
            continue
        opportunity = raw.get("opportunity") or {}
        profile = raw.get("resource_profile") or {}
        work_items = []
        for work in list(raw.get("work_items") or [])[:4]:
            if not isinstance(work, dict):
                continue
            work_items.append(
                {
                    key: work.get(key)
                    for key in (
                        "id",
                        "opportunity_id",
                        "status",
                        "objective",
                        "deliverable_spec",
                        "acceptance_criteria",
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
                    key: opportunity.get(key)
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
                    key: profile.get(key)
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
                "bottleneck_match": raw.get("bottleneck_match"),
                "expected_resource_amount": raw.get("expected_resource_amount"),
                "expected_resource_per_gpu_hour": raw.get("expected_resource_per_gpu_hour"),
                "expected_runway_gain_days": raw.get("expected_runway_gain_days"),
                "work_items": work_items,
            }
        )
    active_work = []
    for work in list(value.get("active_work") or [])[:12]:
        if not isinstance(work, dict):
            continue
        active_work.append(
            {
                key: work.get(key)
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
    return {
        "bottleneck": value.get("bottleneck"),
        "exact_bottleneck_candidate_count": value.get("exact_bottleneck_candidate_count", 0),
        "candidates": candidates,
        "active_work": active_work,
        "unprofiled_opportunity_count": len(value.get("unprofiled_opportunities") or []),
        "epistemic_rule": value.get("epistemic_rule"),
    }


def _bounded_work_ports(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    ports = {}
    for name, item in dict(value.get("ports") or {}).items():
        if not isinstance(item, dict):
            continue
        ports[str(name)[:128]] = {
            key: item.get(key)
            for key in ("server", "submit_tool", "outcome_tool")
            if key in item
        }
    active: list[dict[str, Any]] = []
    for item in list(value.get("active_submissions") or [])[:16]:
        if not isinstance(item, dict):
            continue
        # submission_ref and response_fingerprint stay local; the model can act by
        # work_item_id and the configured port runtime carries the remote reference.
        active.append(
            {
                key: item.get(key)
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
                if key in item
            }
        )
    return {
        "enabled": bool(value.get("enabled", False)),
        "readiness": value.get("readiness"),
        "ports": ports,
        "active_submissions": active,
        "authority_rule": (
            "port configuration fixes transport/server/tool authority; model actions select only declared port/work ids"
        ),
    }


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    path: Path
    text: str
    fingerprint: str

    @classmethod
    def load(cls, path: Path) -> "PromptTemplate":
        path = Path(path)
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("system prompt template is empty")
        return cls(path, text, sha256(text.encode("utf-8")).hexdigest())

    def render(self, context: dict[str, Any]) -> str:
        identity_contract = context.get("identity_contract") or {}
        self_model = context.get("self_model") or {}
        skills = context.get("skills") or {}
        available_skills = {
            name: {
                "maturity": item.get("maturity"),
                "authority": item.get("authority"),
                "description": item.get("description"),
                "procedure": item.get("procedure"),
                "evidence_contract": item.get("evidence_contract"),
            }
            for name, item in skills.items()
            if item.get("available")
        }

        world = context.get("world_model") or {}
        causal = context.get("causal_memory") or {}
        homeostasis = context.get("homeostasis") or {}
        metabolism = context.get("metabolism") or homeostasis.get("metabolism") or {}
        contract = {
            "identity": identity_contract,
            "organism": default_organism_contract(),
            "current_self_model": {
                key: self_model.get(key)
                for key in (
                    "identity_id",
                    "identity_fingerprint",
                    "body_version",
                    "brain_backend",
                    "model_id",
                    "lifecycle_state",
                    "degraded_capabilities",
                    "needs",
                    "homeostasis_mode",
                    "commitments",
                    "uncertainties",
                    "narrative",
                )
                if key in self_model
            },
            "adaptive_self_hypotheses": context.get("self_hypotheses") or [],
            "world_model": {
                "beliefs": list(world.get("beliefs") or [])[:16],
                "contradictions": list(world.get("contradictions") or [])[:8],
                "epistemic_rule": world.get("epistemic_rule"),
            },
            "recent_sensorium": _bounded_sensorium(context.get("sensorium")),
            "causal_strategy_statistics": list(causal.get("strategy_statistics") or [])[:16],
            "metabolism": _bounded_metabolism(metabolism),
            "homeostasis": _bounded_homeostasis(homeostasis),
            "resource_ecology": _bounded_resource_ecology(context.get("resource_ecology")),
            "work_ports": _bounded_work_ports(context.get("work_ports")),
            "executive": context.get("executive") or {},
            "executive_energy": context.get("executive_energy") or {},
            "digital_body": context.get("digital_body") or {},
            "organism_state_bus": context.get("organism_state_bus") or {},
            "metacognitive_calibration": context.get("metacognition") or {},
            "available_skills": available_skills,
        }
        return (
            self.text
            + "\n\n## Verified identity/organism/world/skill contract for this cycle\n"
            + json.dumps(contract, ensure_ascii=False, sort_keys=True)
            + "\n\n## Decision JSON schema\n"
            + json.dumps(DECISION_SCHEMA, ensure_ascii=False, sort_keys=True)
        )
