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


def work(
    work_id: int,
    status: str,
    *,
    updated_at: str,
    opportunity_id: int | None = None,
) -> dict[str, object]:
    return {
        "id": work_id,
        "opportunity_id": opportunity_id or work_id,
        "status": status,
        "objective": f"finish work {work_id}",
        "estimated_gpu_hours": 0.25,
        "updated_at": updated_at,
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
    assert snapshot.continuation_work_item is None
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


def test_continuation_prefers_most_causally_advanced_unfinished_work(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    agency = AgencyKernel(memory)

    snapshot = agency.reconcile(
        [need("goal_formation", 0.6)],
        active_work=[
            work(1, "planned", updated_at="2030-01-01T00:00:00+00:00"),
            work(2, "staged", updated_at="2030-01-02T00:00:00+00:00"),
            work(3, "submitted", updated_at="2030-01-03T00:00:00+00:00"),
            work(4, "accepted", updated_at="2030-01-04T00:00:00+00:00"),
        ],
    )

    assert snapshot.continuation_work_item is not None
    assert snapshot.continuation_work_item["id"] == 4
    assert snapshot.continuation_work_item["status"] == "accepted"


def test_continuation_prevents_newer_work_from_starving_older_same_stage(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    agency = AgencyKernel(memory)

    snapshot = agency.reconcile(
        [],
        active_work=[
            work(8, "submitted", updated_at="2030-01-03T00:00:00+00:00"),
            work(7, "submitted", updated_at="2030-01-01T00:00:00+00:00"),
            work(6, "submitted", updated_at="2030-01-01T00:00:00+00:00"),
        ],
    )

    assert snapshot.continuation_work_item is not None
    assert snapshot.continuation_work_item["id"] == 6


def test_agency_state_and_continuation_are_durable_and_non_authoritative(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    AgencyKernel(memory).reconcile(
        [need("runtime_reliability", 0.82)],
        active_work=[work(21, "staged", updated_at="2030-01-01T00:00:00+00:00")],
    )

    restored = AgencyKernel(MemoryStore(tmp_path / "memory.sqlite3")).snapshot()

    assert restored["selected_need"]["name"] == "runtime_reliability"
    assert restored["focus_goal"]["title"] == "Restore runtime reliability"
    assert restored["continuation_work_item"]["id"] == 21
    assert restored["continuation_work_item"]["status"] == "staged"
    assert "cannot mint capabilities" in restored["authority_rule"]
