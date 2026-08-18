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
            "op": "create|update",
            "id": None,
            "title": "required for create",
            "kind": "work|bounty|grant|free_compute|free_api|product|other",
            "source_url": "https://... or empty if evidence text exists",
            "evidence": "observed provenance; required for terminal state",
            "estimated_value": 0.0,
            "estimated_cost_value": 0.0,
            "unit": "USD|RUB|CREDIT|OTHER",
            "probability": 0.0,
            "estimated_gpu_hours": 0.0,
            "status": "discovered|evaluating|pursuing|won|lost|expired|abandoned",
            "expires_at": None,
            "notes": "optional",
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
