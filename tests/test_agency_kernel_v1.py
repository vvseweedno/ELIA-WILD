from __future__ import annotations

from pathlib import Path

from datetime import datetime, timezone
import math

from elia.agency import AGENCY_SOURCE, BASELINE_GOAL_TITLE, NEED_REGISTRY, AgencyKernel
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
    assert agency.wake_policy(snapshot.as_dict())["max_sleep_seconds"] is None


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
    wake = agency.wake_policy(second.as_dict())
    assert wake["max_sleep_seconds"] == 3600.0
    assert wake["reason"] == "need:capability_repair"


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
        now=datetime(2030, 2, 1, tzinfo=timezone.utc),
        active_work=[
            work(1, "planned", updated_at="2030-01-31T12:00:00+00:00"),
            work(2, "staged", updated_at="2030-01-31T12:00:00+00:00"),
            work(3, "submitted", updated_at="2030-01-31T12:00:00+00:00"),
            work(4, "accepted", updated_at="2030-01-31T12:00:00+00:00"),
        ],
    )

    assert snapshot.continuation_work_item is not None
    assert snapshot.continuation_work_item["id"] == 4
    assert snapshot.continuation_work_item["status"] == "accepted"
    wake = agency.wake_policy(snapshot.as_dict())
    assert wake["max_sleep_seconds"] == 3600.0
    assert wake["reason"] == "work:accepted"
    assert wake["continuation_work_item_id"] == 4


def test_continuation_prevents_newer_work_from_starving_older_same_stage(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    agency = AgencyKernel(memory)

    snapshot = agency.reconcile(
        [],
        now=datetime(2030, 2, 1, tzinfo=timezone.utc),
        active_work=[
            work(8, "submitted", updated_at="2030-01-03T00:00:00+00:00"),
            work(7, "submitted", updated_at="2030-01-01T00:00:00+00:00"),
            work(6, "submitted", updated_at="2030-01-01T00:00:00+00:00"),
        ],
    )

    assert snapshot.continuation_work_item is not None
    assert snapshot.continuation_work_item["id"] == 6


def test_stricter_need_deadline_wins_over_work_deadline(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    agency = AgencyKernel(memory)

    snapshot = agency.reconcile(
        [need("durable_checkpoint", 0.9)],
        now=datetime(2030, 2, 1, tzinfo=timezone.utc),
        active_work=[work(11, "submitted", updated_at="2030-01-01T00:00:00+00:00")],
    )
    wake = agency.wake_policy(snapshot.as_dict())

    assert wake["max_sleep_seconds"] == 1800.0
    assert wake["reason"] == "need:durable_checkpoint"
    assert wake["continuation_work_item_id"] == 11


def test_earliest_deadline_scans_every_active_need_not_only_highest_severity(
    tmp_path: Path,
) -> None:
    agency = AgencyKernel(MemoryStore(tmp_path / "memory.sqlite3"))
    snapshot = agency.reconcile(
        [
            need("runtime_reliability", 0.99),  # selected; 1-hour cap
            need("storage_survival", 0.80),  # lower severity; 15-minute cap
        ]
    )

    wake = agency.wake_policy(snapshot.as_dict())

    assert snapshot.selected_need is not None
    assert snapshot.selected_need["name"] == "runtime_reliability"
    assert wake["max_sleep_seconds"] == 900.0
    assert wake["reason"] == "need:storage_survival"
    assert wake["active_need_names"] == ["runtime_reliability", "storage_survival"]


def test_agency_state_and_continuation_are_durable_and_non_authoritative(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    AgencyKernel(memory).reconcile(
        [need("runtime_reliability", 0.82)],
        now=datetime(2030, 2, 1, tzinfo=timezone.utc),
        active_work=[work(21, "staged", updated_at="2030-01-01T00:00:00+00:00")],
    )

    restored_agency = AgencyKernel(MemoryStore(tmp_path / "memory.sqlite3"))
    restored = restored_agency.snapshot()

    assert restored["selected_need"]["name"] == "runtime_reliability"
    assert restored["focus_goal"]["title"] == "Restore runtime reliability"
    assert restored["continuation_work_item"]["id"] == 21
    assert restored["continuation_work_item"]["status"] == "staged"
    assert "cannot mint capabilities" in restored["authority_rule"]
    assert restored_agency.wake_policy(restored)["max_sleep_seconds"] == 3600.0


def test_need_registry_is_exhaustive_and_typed_for_all_current_producers() -> None:
    expected = {
        "continuity_integrity",
        "identity_drift",
        "durable_checkpoint",
        "compute_survival",
        "compute_conservation",
        "resource_acquisition",
        "resource_execution",
        "resource_discovery",
        "work_execution",
        "opportunity_review",
        "opportunity_discovery",
        "body_readiness",
        "runtime_reliability",
        "capability_repair",
        "goal_formation",
        "goal_unblocking",
        "resource_runway",
        "uncovered_essential_obligation",
        "storage_survival",
        "storage_conservation",
        "state_reconciliation",
        "sensorium_degradation",
        "epistemic_conflict",
    }
    assert set(NEED_REGISTRY) == expected
    for name, spec in NEED_REGISTRY.items():
        assert spec.name == name
        assert spec.goal_title and spec.goal_description
        assert math.isfinite(spec.wake_cap_seconds) and spec.wake_cap_seconds > 0


def test_unknown_and_nonfinite_needs_cannot_enter_agency_state(tmp_path: Path) -> None:
    agency = AgencyKernel(MemoryStore(tmp_path / "memory.sqlite3"))
    snapshot = agency.reconcile(
        [
            need("not_a_registered_need", 1.0),
            need("runtime_reliability", float("nan")),
        ]
    )
    assert snapshot.selected_need is not None
    assert snapshot.selected_need["name"] == "runtime_reliability"
    assert snapshot.selected_need["severity"] == 0.0


def test_work_aging_eventually_prevents_cross_stage_starvation(tmp_path: Path) -> None:
    agency = AgencyKernel(MemoryStore(tmp_path / "memory.sqlite3"))
    now = datetime(2030, 2, 1, tzinfo=timezone.utc)
    selected = agency.reconcile(
        [],
        now=now,
        active_work=[
            work(1, "planned", updated_at="2030-01-01T00:00:00+00:00"),
            work(2, "accepted", updated_at="2030-02-01T00:00:00+00:00"),
        ],
    ).continuation_work_item
    assert selected is not None
    assert selected["id"] == 1
    assert selected["aging_priority_boost"] >= 10


def test_work_selection_is_permutation_invariant(tmp_path: Path) -> None:
    agency = AgencyKernel(MemoryStore(tmp_path / "memory.sqlite3"))
    now = datetime(2030, 2, 1, tzinfo=timezone.utc)
    items = [
        work(9, "submitted", updated_at="2030-01-31T00:00:00+00:00"),
        work(4, "staged", updated_at="2030-01-20T00:00:00+00:00"),
        work(7, "planned", updated_at="2030-01-01T00:00:00+00:00"),
    ]
    forward = agency._continuation_work(items, now=now)
    backward = agency._continuation_work(list(reversed(items)), now=now)
    assert forward is not None and backward is not None
    assert forward["id"] == backward["id"]


def test_full_goal_capacity_cannot_hide_a_critical_continuity_focus(
    tmp_path: Path,
) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    first = memory.create_goal("User goal A", "Preserve it", priority=1.0, source="owner")
    second = memory.create_goal("User goal B", "Preserve it", priority=1.0, source="owner")
    agency = AgencyKernel(memory, max_active_goals=2)

    snapshot = agency.reconcile([need("continuity_integrity", 1.0)])

    assert snapshot.focus_goal is not None
    assert snapshot.focus_goal["title"] == "Restore trusted continuity integrity"
    assert snapshot.focus_goal["source"] == AGENCY_SOURCE
    assert snapshot.selected_need is not None
    assert snapshot.selected_need["name"] == "continuity_integrity"
    # The emergency commitment reserves attention without deleting user commitments.
    active_ids = {goal.id for goal in memory.active_goals()}
    assert {first, second} <= active_ids


def test_future_work_timestamp_cannot_suppress_fair_aging(tmp_path: Path) -> None:
    agency = AgencyKernel(MemoryStore(tmp_path / "memory.sqlite3"))
    now = datetime(2030, 2, 1, tzinfo=timezone.utc)

    selected = agency.reconcile(
        [],
        now=now,
        active_work=[
            work(1, "accepted", updated_at="2099-01-01T00:00:00+00:00"),
            work(2, "planned", updated_at="2030-01-01T00:00:00+00:00"),
        ],
    ).continuation_work_item

    assert selected is not None
    assert selected["id"] == 2
