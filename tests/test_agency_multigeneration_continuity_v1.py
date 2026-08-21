from __future__ import annotations

from pathlib import Path

from elia.agency import AgencyKernel
from elia.checkpoint import CheckpointManager
from elia.chronicle import Chronicle
from elia.economy import EconomyStore
from elia.memory import MemoryStore
from elia.observations import ObservationStore
from elia.resource_ecology import ResourceEcologyStore


AUTH_KEY = b"multigeneration-auth-key-32-bytes!!"
ENC_KEY = b"m" * 32


def manager(state_dir: Path) -> CheckpointManager:
    return CheckpointManager(
        state_dir,
        "ELIA",
        AUTH_KEY,
        encryption_key=ENC_KEY,
        require_encryption=True,
    )


def active_work(state_dir: Path) -> list[dict[str, object]]:
    return [item.as_dict() for item in ResourceEcologyStore(state_dir / "memory.sqlite3").active_work()]


def export_generation(state_dir: Path, path: Path):
    return manager(state_dir).export(path)


def restore_generation(checkpoint: Path, state_dir: Path, digest: str) -> None:
    manager(state_dir).restore(checkpoint, expected_digest=digest)
    assert Chronicle(state_dir / "chronicle.jsonl").verify() == (True, None)


def test_one_commitment_and_work_item_survive_four_process_generations(tmp_path: Path) -> None:
    # Generation 1: form a model-independent commitment and create one real persisted
    # work item. From this point onward every generation is reconstructed from a sealed
    # checkpoint rather than sharing Python objects or an in-memory runtime.
    first = tmp_path / "generation-1" / ".elia"
    db = first / "memory.sqlite3"
    memory = MemoryStore(db)
    chronicle = Chronicle(first / "chronicle.jsonl")
    chronicle.append("GENERATION", {"generation": 1})

    economy = EconomyStore(db)
    ecology = ResourceEcologyStore(db)
    opportunity_id = economy.create_opportunity(
        title="Persistent continuity work",
        kind="work",
        evidence="deterministic multigeneration test fixture",
        estimated_value=10.0,
        probability=0.8,
        estimated_gpu_hours=0.5,
        source="test",
    )
    ecology.upsert_profile(
        opportunity_id=opportunity_id,
        target_asset="compute",
        target_unit="GPU_HOUR",
        target_amount=2.0,
        eligibility_confidence=0.9,
        evidence_quality=0.9,
        evidence="test fixture has a typed target",
        source="test",
    )
    planned = ecology.create_work_item(
        opportunity_id=opportunity_id,
        objective="Carry one unfinished task across substrate exits",
        deliverable_spec="A persisted deliverable that remains tied to this work item",
        acceptance_criteria="The same work id remains causally active after restore",
        estimated_gpu_hours=0.25,
        source="test",
    )
    agency = AgencyKernel(memory)
    state_1 = agency.reconcile([], active_work=active_work(first))
    assert state_1.focus_goal is not None
    assert state_1.continuation_work_item is not None
    focus_goal_id = int(state_1.focus_goal["id"])
    assert state_1.continuation_work_item["id"] == planned.id
    assert state_1.continuation_work_item["status"] == "planned"

    cp1 = export_generation(first, tmp_path / "generation-1.eliacp")

    # Generation 2: a new state directory is born only from the encrypted checkpoint.
    # It stages the same work item and updates the durable continuation cursor.
    second = tmp_path / "generation-2" / ".elia"
    restore_generation(cp1.path, second, cp1.digest)
    memory_2 = MemoryStore(second / "memory.sqlite3")
    restored_1 = AgencyKernel(memory_2).snapshot()
    assert restored_1["focus_goal"]["id"] == focus_goal_id
    assert restored_1["continuation_work_item"]["id"] == planned.id
    assert restored_1["continuation_work_item"]["status"] == "planned"

    workspace = second / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    deliverable = workspace / "persistent-deliverable.md"
    deliverable.write_text("same causal work item\n", encoding="utf-8")
    ecology_2 = ResourceEcologyStore(second / "memory.sqlite3")
    staged = ecology_2.attach_staged_deliverable(
        opportunity_id=opportunity_id,
        artifact_path="workspace/persistent-deliverable.md",
        evidence="generation 2 staged the persisted deliverable",
    )
    state_2 = AgencyKernel(memory_2).reconcile([], active_work=active_work(second))
    assert state_2.focus_goal is not None
    assert state_2.focus_goal["id"] == focus_goal_id
    assert state_2.continuation_work_item is not None
    assert state_2.continuation_work_item["id"] == staged.id == planned.id
    assert state_2.continuation_work_item["status"] == "staged"
    Chronicle(second / "chronicle.jsonl").append("GENERATION", {"generation": 2})
    cp2 = export_generation(second, tmp_path / "generation-2.eliacp")
    assert cp2.counter > cp1.counter

    # Generation 3: reconstruct again, then record a successful external-submission
    # observation and advance *the same* work item to submitted.
    third = tmp_path / "generation-3" / ".elia"
    restore_generation(cp2.path, third, cp2.digest)
    memory_3 = MemoryStore(third / "memory.sqlite3")
    restored_2 = AgencyKernel(memory_3).snapshot()
    assert restored_2["focus_goal"]["id"] == focus_goal_id
    assert restored_2["continuation_work_item"]["id"] == planned.id
    assert restored_2["continuation_work_item"]["status"] == "staged"

    observation = ObservationStore(third / "memory.sqlite3").record(
        source_kind="external_work",
        source_ref="test-submit",
        payload={"work_item_id": planned.id, "accepted_by_transport": True},
        trust=1.0,
        success=True,
        summary="submission transport accepted the same persisted work item",
        provenance={"fixture": "multigeneration"},
    )
    ecology_3 = ResourceEcologyStore(third / "memory.sqlite3")
    submitted = ecology_3.record_submission(
        work_item_id=planned.id,
        observation_id=observation.id,
        evidence="generation 3 has a successful recorded submission observation",
    )
    state_3 = AgencyKernel(memory_3).reconcile([], active_work=active_work(third))
    assert state_3.focus_goal is not None
    assert state_3.focus_goal["id"] == focus_goal_id
    assert state_3.continuation_work_item is not None
    assert state_3.continuation_work_item["id"] == submitted.id == planned.id
    assert state_3.continuation_work_item["status"] == "submitted"
    Chronicle(third / "chronicle.jsonl").append("GENERATION", {"generation": 3})
    cp3 = export_generation(third, tmp_path / "generation-3.eliacp")
    assert cp3.counter > cp2.counter

    # Generation 4: another fresh process state observes the unresolved submission,
    # records an evidence-backed external acceptance, and still retains the original
    # goal/work identity. Accepted remains unfinished until a separately verified
    # resource event proves realization, so the continuation cursor must remain active.
    fourth = tmp_path / "generation-4" / ".elia"
    restore_generation(cp3.path, fourth, cp3.digest)
    memory_4 = MemoryStore(fourth / "memory.sqlite3")
    restored_3 = AgencyKernel(memory_4).snapshot()
    assert restored_3["focus_goal"]["id"] == focus_goal_id
    assert restored_3["continuation_work_item"]["id"] == planned.id
    assert restored_3["continuation_work_item"]["status"] == "submitted"

    ecology_4 = ResourceEcologyStore(fourth / "memory.sqlite3")
    accepted = ecology_4.record_external_outcome(
        work_item_id=planned.id,
        accepted=True,
        evidence="generation 4 received evidence-backed acceptance",
    )
    state_4 = AgencyKernel(memory_4).reconcile([], active_work=active_work(fourth))
    assert state_4.focus_goal is not None
    assert state_4.focus_goal["id"] == focus_goal_id
    assert state_4.continuation_work_item is not None
    assert state_4.continuation_work_item["id"] == accepted.id == planned.id
    assert state_4.continuation_work_item["status"] == "accepted"

    # The core invariant: four independent substrate directories, one durable goal id,
    # one durable work id, monotonically advancing work state, authenticated checkpoints,
    # and no reconstruction of intent from a new language-model answer.
    assert MemoryStore(fourth / "memory.sqlite3").goal(focus_goal_id) is not None
    assert ResourceEcologyStore(fourth / "memory.sqlite3").work_item(planned.id) is not None
