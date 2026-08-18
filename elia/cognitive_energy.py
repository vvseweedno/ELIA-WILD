from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .executive import CognitiveBudget, ExecutivePlan, ExecutivePolicy


@dataclass(frozen=True, slots=True)
class CognitiveEnergySummary:
    resolved_cycles: int
    total_target_seconds: float
    total_actual_seconds: float
    actual_to_target_ratio: float | None
    overspend_cycles: int
    state: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "resolved_cycles": self.resolved_cycles,
            "total_target_seconds": self.total_target_seconds,
            "total_actual_seconds": self.total_actual_seconds,
            "actual_to_target_ratio": self.actual_to_target_ratio,
            "overspend_cycles": self.overspend_cycles,
            "state": self.state,
        }


class CognitiveEnergyController:
    """Convert measured inference cost into a conservative future budget correction.

    The feedback loop can only reduce the next model budget. It never broadens
    authority, never invents compute, and never upgrades a low-cost plan into deeper
    reasoning just because prior calls were cheap.
    """

    def __init__(self, policy: ExecutivePolicy | None = None, *, window: int = 12):
        self.policy = policy or ExecutivePolicy()
        self.window = max(3, min(int(window), 100))

    def summarize(self, rows: list[dict[str, Any]]) -> CognitiveEnergySummary:
        usable: list[tuple[float, float]] = []
        for row in rows[-self.window :]:
            if not bool(row.get("brain_wake")):
                continue
            actual_raw = row.get("brain_seconds_used")
            if actual_raw is None:
                continue
            try:
                target = max(0.0, float(row.get("target_brain_seconds") or 0.0))
                actual = max(0.0, float(actual_raw))
            except (TypeError, ValueError):
                continue
            if target <= 0:
                continue
            usable.append((target, actual))

        target_total = sum(item[0] for item in usable)
        actual_total = sum(item[1] for item in usable)
        ratio = actual_total / target_total if target_total > 0 else None
        overspend = sum(1 for target, actual in usable if actual > target * 1.5)
        if ratio is None:
            state = "unmeasured"
        elif len(usable) >= 3 and ratio >= 2.0:
            state = "severe_overspend"
        elif len(usable) >= 3 and ratio >= 1.5:
            state = "overspend"
        elif len(usable) >= 3 and ratio <= 0.75:
            state = "efficient"
        else:
            state = "nominal"
        return CognitiveEnergySummary(
            resolved_cycles=len(usable),
            total_target_seconds=target_total,
            total_actual_seconds=actual_total,
            actual_to_target_ratio=ratio,
            overspend_cycles=overspend,
            state=state,
        )

    def constrain(self, plan: ExecutivePlan, summary: CognitiveEnergySummary) -> ExecutivePlan:
        budget = plan.cognitive_budget
        if not budget.wake_brain or summary.resolved_cycles < 3:
            return plan

        replacement: CognitiveBudget | None = None
        if summary.state == "severe_overspend":
            if budget.tier in {"deep", "normal"}:
                replacement = CognitiveBudget(
                    "low",
                    True,
                    self.policy.low_tokens,
                    self.policy.low_target_brain_seconds,
                    False,
                )
        elif summary.state == "overspend" and budget.tier == "deep":
            replacement = CognitiveBudget(
                "normal",
                True,
                self.policy.normal_tokens,
                self.policy.normal_target_brain_seconds,
                False,
            )

        if replacement is None:
            return plan

        ratio = summary.actual_to_target_ratio or 0.0
        return ExecutivePlan(
            mode=plan.mode,
            focus=plan.focus,
            cognitive_budget=replacement,
            sleep_seconds=plan.sleep_seconds,
            budget_ratio=plan.budget_ratio,
            interrupt=plan.interrupt,
            reasons=plan.reasons
            + (
                f"Cognitive energy feedback constrained inference: recent actual/target brain-seconds ratio={ratio:.3f} across {summary.resolved_cycles} resolved cycle(s).",
            ),
        )
