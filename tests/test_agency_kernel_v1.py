from __future__ import annotations

from pathlib import Path

from elia.agency import AGENCY_SOURCE, BASELINE_GOAL_TITLE, AgencyKernel
from elia.memory import MemoryStore


def need(name: str, severity: float, reason: str = "verified pressure") -> dict[str, object]:
    return {
        "name": name,
        "severity": severity,
        "reason": reason,
        "response_hint": "take the smallest bounded step",
        "source": "test",
    }


def test_agency_forms_baseline_commitment_without_model(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    agency = AgencyKernel(memory)

    snapshot = agency.reconcile([need("goal_formation", 0.6)])

    goals = memory.active_goals()
    assert len(goals) == 1
    assert goals[0].title == BASELINE_GOAL_TITLE
    assert goals[0].source == AGENCY_SOURCE
    assert snapshot.focus_goal is not None
    assert snapshot.focus_goal["id"] == goals[0].id
    assert snapshot.selected_need is None
    assert snapshot.created_goal_ids == (goals[0].id,)


def test_urgent_verified_need_preempts_weaker_goal_without_replacing_it(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    existing_id = memory.create_goal(
        "Explore a low-priority hypothesis",
        "Keep this reversible and evidence driven.",
        priority=0.4,
        source="brain",
    )
    agency = AgencyKernel(memory)

    first = agency.reconcile([need("capability_repair", 0.9)])
    second = agency.reconcile([need("capability_repair", 0.9)])

    goals = memory.active_goals()
    repair = [goal for goal in goals if goal.title == "Repair degraded capabilities"]
    assert len(repair) == 1
    assert repair[0].source == AGENCY_SOURCE
    assert first.focus_goal is not None
    assert first.focus_goal["id"] == repair[0].id
    assert second.focus_goal is not None
    assert second.focus_goal["id"] == repair[0].id
    assert second.created_goal_ids == ()
    assert memory.goal(existing_id) is not None
    assert memory.goal(existing_id).status == "active"  # type: ignore[union-attr]


def test_resolved_maintenance_pressure_closes_agency_goal_deterministically(
    tmp_path: Path,
) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    agency = AgencyKernel(memory)

    first = agency.reconcile([need("durable_checkpoint", 0.85)])
    assert first.focus_goal is not None
    maintenance_id = int(first.focus_goal["id"])

    second = agency.reconcile([need("goal_formation", 0.6)])

    resolved = memory.goal(maintenance_id)
    assert resolved is not None
    assert resolved.status == "completed"
    assert maintenance_id in second.resolved_goal_ids
    assert any(goal.title == BASELINE_GOAL_TITLE for goal in memory.active_goals())
    events = memory.goal_events(maintenance_id)
    assert events[-1]["kind"] == "agency_resolved"
    assert "absent from the current verified organism state" in events[-1]["content"]


def test_agency_state_is_durable_and_explicitly_non_authoritative(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    AgencyKernel(memory).reconcile([need("runtime_reliability", 0.82)])

    restored = AgencyKernel(MemoryStore(tmp_path / "memory.sqlite3")).snapshot()

    assert restored["selected_need"]["name"] == "runtime_reliability"
    assert restored["focus_goal"]["title"] == "Restore runtime reliability"
    assert "cannot mint capabilities" in restored["authority_rule"]
