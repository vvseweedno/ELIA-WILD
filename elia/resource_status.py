from __future__ import annotations

from copy import deepcopy
from typing import Any

from .config import Config
from .resource_ecology import ResourceEcologyEngine


def resource_ecology_status(
    config: Config,
    *,
    metabolism_snapshot: dict[str, Any],
    limit: int = 16,
) -> dict[str, Any]:
    """Return the deterministic local read-only resource-ecology projection."""

    engine = ResourceEcologyEngine(config.runtime.state_dir / "memory.sqlite3")
    return engine.snapshot(
        metabolism_snapshot,
        limit=max(1, min(int(limit), 64)),
    )


def _resource_need_severity(runway_days: float | None) -> float:
    if runway_days is None:
        return 0.45
    value = max(0.0, float(runway_days))
    if value <= 3:
        return 0.94
    if value <= 7:
        return 0.84
    if value <= 14:
        return 0.72
    if value <= 30:
        return 0.58
    return 0.42


def resource_ecology_needs(value: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive deterministic typed-resource/work pressures from an ecology snapshot."""

    needs: list[dict[str, Any]] = []
    bottleneck = value.get("bottleneck")
    if isinstance(bottleneck, dict):
        runway_raw = bottleneck.get("runway_days")
        runway = float(runway_raw) if runway_raw is not None else None
        severity = _resource_need_severity(runway)
        exact_count = int(value.get("exact_bottleneck_candidate_count", 0) or 0)
        if exact_count > 0:
            needs.append(
                {
                    "name": "resource_execution",
                    "severity": severity,
                    "reason": (
                        f"Verified bottleneck {bottleneck.get('asset')}/{bottleneck.get('unit')} "
                        f"has {runway_raw!r} runway days and {exact_count} exact typed candidate(s)."
                    ),
                    "response_hint": (
                        "Prefer evidence-backed qualification or progress on the best exact resource candidate; "
                        "do not treat expected reward as received resource."
                    ),
                    "source": "resource_ecology",
                    "evidence": {
                        "asset": bottleneck.get("asset"),
                        "unit": bottleneck.get("unit"),
                        "runway_days": runway_raw,
                        "exact_candidate_count": exact_count,
                    },
                }
            )
        else:
            needs.append(
                {
                    "name": "resource_discovery",
                    "severity": severity,
                    "reason": (
                        f"Verified bottleneck {bottleneck.get('asset')}/{bottleneck.get('unit')} "
                        f"has {runway_raw!r} runway days but no exact typed opportunity candidate."
                    ),
                    "response_hint": (
                        "Search for legitimate opportunities that explicitly target this exact resource key; "
                        "do not substitute unrelated currencies, credits or abstract value."
                    ),
                    "source": "resource_ecology",
                    "evidence": {
                        "asset": bottleneck.get("asset"),
                        "unit": bottleneck.get("unit"),
                        "runway_days": runway_raw,
                        "exact_candidate_count": 0,
                    },
                }
            )

    active_work = [item for item in (value.get("active_work") or []) if isinstance(item, dict)]
    if active_work:
        staged = sum(1 for item in active_work if item.get("status") == "staged")
        submitted = sum(1 for item in active_work if item.get("status") == "submitted")
        needs.append(
            {
                "name": "work_execution",
                "severity": 0.74 if staged or submitted else 0.62,
                "reason": (
                    f"{len(active_work)} active resource work item(s) exist; "
                    f"{staged} staged and {submitted} submitted."
                ),
                "response_hint": (
                    "Advance one evidence-backed work item using only currently authorized capabilities. "
                    "A local artifact is not submission, and submission is not payment."
                ),
                "source": "resource_ecology",
            }
        )
    return needs


def public_resource_ecology(value: dict[str, Any]) -> dict[str, Any]:
    """Return an evidence-minimized view safe for MCP/status export.

    Raw qualification evidence, opportunity notes, deliverable acceptance text and
    external responses remain local. The exported view retains exact resource keys,
    lifecycle state, scores and provenance identifiers needed for coordination.
    """

    candidates: list[dict[str, Any]] = []
    for raw in list(value.get("candidates") or [])[:16]:
        if not isinstance(raw, dict):
            continue
        opportunity = raw.get("opportunity") if isinstance(raw.get("opportunity"), dict) else {}
        profile = (
            raw.get("resource_profile") if isinstance(raw.get("resource_profile"), dict) else {}
        )
        work_items: list[dict[str, Any]] = []
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

    active_work: list[dict[str, Any]] = []
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

    return {
        "bottleneck": deepcopy(value.get("bottleneck")),
        "exact_bottleneck_candidate_count": int(
            value.get("exact_bottleneck_candidate_count", 0) or 0
        ),
        "candidates": candidates,
        "active_work": active_work,
        "unprofiled_opportunity_count": len(value.get("unprofiled_opportunities") or []),
        "epistemic_rule": str(value.get("epistemic_rule", ""))[:2000],
    }
