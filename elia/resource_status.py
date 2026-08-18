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
