from __future__ import annotations

from elia.cognitive_energy import CognitiveEnergyController
from elia.executive import ExecutiveController, ExecutivePolicy


def _mission_plan():
    context = {
        "resources": {"weekly_limit_hours": 30.0, "runtime_hours_remaining": 30.0},
        "needs": [],
        "active_goals": [
            {"id": 1, "title": "High-value mission", "priority": 0.95, "status": "active"}
        ],
        "chronicle_integrity": {"valid": True, "error": None},
        "identity_drift": {"status": "stable"},
    }
    return ExecutiveController(ExecutivePolicy(deep_focus_threshold=0.8)).plan(context)


def test_unmeasured_energy_does_not_change_plan() -> None:
    controller = CognitiveEnergyController()
    summary = controller.summarize([])
    plan = _mission_plan()
    adjusted = controller.constrain(plan, summary)
    assert summary.state == "unmeasured"
    assert adjusted == plan


def test_repeated_overspend_downgrades_deep_to_normal() -> None:
    controller = CognitiveEnergyController()
    rows = [
        {
            "brain_wake": 1,
            "target_brain_seconds": 20.0,
            "brain_seconds_used": 34.0,
        }
        for _ in range(3)
    ]
    summary = controller.summarize(rows)
    assert summary.state == "overspend"
    plan = _mission_plan()
    assert plan.cognitive_budget.tier == "deep"
    adjusted = controller.constrain(plan, summary)
    assert adjusted.cognitive_budget.tier == "normal"
    assert adjusted.cognitive_budget.allow_thinking is False
    assert any("Cognitive energy feedback constrained" in item for item in adjusted.reasons)


def test_severe_overspend_downgrades_normal_or_deep_to_low() -> None:
    controller = CognitiveEnergyController()
    rows = [
        {
            "brain_wake": 1,
            "target_brain_seconds": 10.0,
            "brain_seconds_used": 25.0,
        }
        for _ in range(4)
    ]
    summary = controller.summarize(rows)
    assert summary.state == "severe_overspend"
    plan = _mission_plan()
    adjusted = controller.constrain(plan, summary)
    assert adjusted.cognitive_budget.tier == "low"
    assert adjusted.cognitive_budget.max_tokens == 256
    assert adjusted.cognitive_budget.allow_thinking is False


def test_energy_feedback_never_upgrades_a_low_cost_plan() -> None:
    controller = CognitiveEnergyController()
    efficient = controller.summarize(
        [
            {
                "brain_wake": 1,
                "target_brain_seconds": 20.0,
                "brain_seconds_used": 2.0,
            }
            for _ in range(4)
        ]
    )
    context = {
        "resources": {"weekly_limit_hours": 30.0, "runtime_hours_remaining": 2.0},
        "needs": [
            {
                "name": "resource_acquisition",
                "severity": 0.7,
                "reason": "Runway is low.",
                "response_hint": "Use cheap cognition.",
            }
        ],
        "active_goals": [],
        "chronicle_integrity": {"valid": True},
        "identity_drift": {"status": "stable"},
    }
    plan = ExecutiveController().plan(context)
    assert plan.cognitive_budget.tier == "low"
    adjusted = controller.constrain(plan, efficient)
    assert adjusted.cognitive_budget.tier == "low"
