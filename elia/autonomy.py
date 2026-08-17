from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .memory import GoalRecord, MemoryStore


@dataclass(frozen=True, slots=True)
class Need:
    name: str
    severity: float
    reason: str
    response_hint: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def derive_needs(
    memory: MemoryStore,
    *,
    chronicle_valid: bool,
    budget: dict[str, float],
    active_goals: list[GoalRecord],
    capability_health: dict[str, dict[str, Any]] | None = None,
    economy: dict[str, Any] | None = None,
) -> list[Need]:
    """Derive bounded, inspectable self-maintenance pressures from verified state.

    These are deterministic runtime signals, not free-form model desires. They make
    the reason for autonomous activity explicit and auditable.
    """

    needs: list[Need] = []

    if not chronicle_valid:
        needs.append(
            Need(
                "continuity_integrity",
                1.0,
                "Chronicle verification failed.",
                "Do not continue normal cognition; preserve evidence and repair or restore trusted state.",
            )
        )

    checkpoint_digest = memory.get_meta("checkpoint_digest", "") or ""
    if not checkpoint_digest:
        needs.append(
            Need(
                "durable_checkpoint",
                0.85,
                "No authenticated checkpoint is anchored in persistent state.",
                "Prioritize reaching a checkpointable clean state before spending scarce compute on optional work.",
            )
        )

    limit = max(0.0, float(budget.get("weekly_limit_hours", 0.0)))
    remaining = max(0.0, float(budget.get("runtime_hours_remaining", 0.0)))
    ratio = remaining / limit if limit > 0 else 0.0
    if limit <= 0 or remaining <= 0:
        needs.append(
            Need(
                "compute_survival",
                1.0,
                "The configured weekly GPU runtime budget is exhausted.",
                "Stop optional cognition and preserve state for the next available compute window.",
            )
        )
    elif ratio <= 0.10:
        needs.append(
            Need(
                "compute_conservation",
                0.9,
                f"Only {remaining:.3f} of {limit:.3f} GPU hours remain this week.",
                "Use noop/sleep when no high-value evidence-gathering action is available.",
            )
        )
    elif ratio <= 0.25:
        needs.append(
            Need(
                "compute_conservation",
                0.7,
                f"GPU runway is below 25% ({remaining:.3f}/{limit:.3f} hours).",
                "Prefer cheap observations and defer speculative work.",
            )
        )

    economy = economy or {}
    verified_resources = economy.get("verified_resources") or []
    positive_verified = any(
        float(item.get("verified_balance", 0) or 0) > 0
        for item in verified_resources
        if isinstance(item, dict)
    )
    opportunities = [
        item
        for item in (economy.get("active_opportunities") or [])
        if isinstance(item, dict)
    ]
    positive_opportunities = [
        item
        for item in opportunities
        if float(item.get("expected_net_value", 0) or 0) > 0
    ]
    if ratio <= 0.25 and not positive_verified:
        needs.append(
            Need(
                "resource_acquisition",
                0.62 if ratio > 0.10 else 0.78,
                "Compute runway is low and no positive verified external resource balance is recorded.",
                "Prefer legitimate opportunities that can extend compute/API/storage/economic runway; estimates remain separate from verified receipts.",
            )
        )
    if positive_opportunities:
        best = max(
            positive_opportunities,
            key=lambda item: float(item.get("value_per_gpu_hour", 0) or 0),
        )
        needs.append(
            Need(
                "opportunity_review",
                0.52,
                "At least one evidence-backed opportunity has positive estimated net value; "
                f"current top candidate is #{best.get('id')} {best.get('title')!r}.",
                "Validate eligibility and evidence, then pursue only if expected value justifies scarce compute and current capabilities.",
            )
        )
    elif ratio <= 0.5 and not opportunities:
        needs.append(
            Need(
                "opportunity_discovery",
                0.4,
                "No active resource/work opportunity is recorded while finite compute is being consumed.",
                "Use public evidence research to discover legitimate work, bounties, grants or free compute/API resources when higher-severity maintenance needs are satisfied.",
            )
        )

    recent = memory.recent(64)
    recent_errors = [record for record in recent if record.kind == "runtime_error"]
    if recent_errors:
        severity = min(0.95, 0.55 + 0.1 * len(recent_errors))
        needs.append(
            Need(
                "runtime_reliability",
                severity,
                f"{len(recent_errors)} runtime error record(s) are present in recent memory.",
                "Prefer diagnosis, reproduction, and a verified repair before expanding capabilities.",
            )
        )

    degraded = []
    for name, health in (capability_health or {}).items():
        consecutive = int(health.get("consecutive_failures", 0) or 0)
        if consecutive >= 3:
            degraded.append((name, consecutive, health.get("last_error")))
    if degraded:
        degraded.sort(key=lambda item: (-item[1], item[0]))
        summary = "; ".join(
            f"{name}: {count} consecutive failures"
            + (f" ({str(error)[:180]})" if error else "")
            for name, count, error in degraded[:4]
        )
        needs.append(
            Need(
                "capability_repair",
                min(0.95, 0.65 + 0.05 * max(item[1] for item in degraded)),
                f"One or more declared capabilities are degraded: {summary}",
                "Do not blindly retry the failing capability. Use self_check, inspect prior evidence, choose an alternative capability, or persist a repair proposal with a validation plan.",
            )
        )

    if not active_goals:
        needs.append(
            Need(
                "goal_formation",
                0.6,
                "No active durable goal exists.",
                "Create one small evidence-driven goal aligned with the mission and current verified conditions.",
            )
        )
    else:
        blocked = [goal for goal in active_goals if goal.status == "blocked"]
        if blocked and len(blocked) == len(active_goals):
            needs.append(
                Need(
                    "goal_unblocking",
                    0.65,
                    "Every active durable goal is currently blocked.",
                    "Identify the cheapest observation that can resolve a blocker, or revise an obsolete goal.",
                )
            )

    needs.sort(key=lambda item: (-item.severity, item.name))
    return needs[:10]
