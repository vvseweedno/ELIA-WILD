from __future__ import annotations

from pathlib import Path

from elia.autonomy import derive_needs
from elia.memory import MemoryStore


def names(needs) -> set[str]:
    return {need.name for need in needs}


def test_needs_emerge_from_verified_state(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    budget = {
        "weekly_limit_hours": 30.0,
        "runtime_hours_used": 0.0,
        "brain_hours_used": 0.0,
        "runtime_hours_remaining": 30.0,
    }

    needs = derive_needs(memory, chronicle_valid=True, budget=budget, active_goals=[])
    assert "durable_checkpoint" in names(needs)
    assert "goal_formation" in names(needs)
    assert "compute_conservation" not in names(needs)


def test_needs_change_when_state_changes(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    memory.set_meta("checkpoint_digest", "a" * 64)
    memory.create_goal("Preserve continuity", priority=0.9)
    budget = {
        "weekly_limit_hours": 30.0,
        "runtime_hours_used": 28.0,
        "brain_hours_used": 1.0,
        "runtime_hours_remaining": 2.0,
    }

    needs = derive_needs(
        memory,
        chronicle_valid=True,
        budget=budget,
        active_goals=memory.active_goals(),
    )
    assert "durable_checkpoint" not in names(needs)
    assert "goal_formation" not in names(needs)
    assert "compute_conservation" in names(needs)


def test_recent_runtime_errors_create_reliability_need(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    memory.set_meta("checkpoint_digest", "b" * 64)
    memory.create_goal("Keep runtime healthy")
    memory.remember("runtime_error", "synthetic failure", source="test", importance=0.9)
    budget = {
        "weekly_limit_hours": 30.0,
        "runtime_hours_used": 1.0,
        "brain_hours_used": 0.1,
        "runtime_hours_remaining": 29.0,
    }

    needs = derive_needs(
        memory,
        chronicle_valid=True,
        budget=budget,
        active_goals=memory.active_goals(),
    )
    assert "runtime_reliability" in names(needs)


def test_invalid_chronicle_is_highest_priority_need(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    budget = {
        "weekly_limit_hours": 30.0,
        "runtime_hours_used": 0.0,
        "brain_hours_used": 0.0,
        "runtime_hours_remaining": 30.0,
    }

    needs = derive_needs(memory, chronicle_valid=False, budget=budget, active_goals=[])
    assert needs[0].name == "continuity_integrity"
    assert needs[0].severity == 1.0
