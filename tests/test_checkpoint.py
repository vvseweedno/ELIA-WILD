from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import stat
import zipfile

import pytest

from elia.checkpoint import (
    CheckpointAuthenticationError,
    CheckpointError,
    CheckpointManager,
    CheckpointRollbackError,
)
from elia.chronicle import Chronicle
from elia.memory import MemoryStore
from elia.owner_control import HumanApprovalRequired, OwnerControl, OwnerMandate
from elia.transition_kernel import state_writer_lock_path


KEY = b"genesis-test-key-32-bytes-long!!"
WAKE_AT = "2030-01-02T03:04:05+00:00"


def _concurrent_export(state: str, destination: str, queue) -> None:
    try:
        info = CheckpointManager(Path(state), "ELIA", KEY).export(Path(destination))
        queue.put(("ok", info.counter, info.digest))
    except Exception as exc:  # pragma: no cover - asserted through the child result.
        queue.put(("error", type(exc).__name__, str(exc)))


def seed_state(state_dir: Path, value: str = "alpha") -> None:
    memory = MemoryStore(state_dir / "memory.sqlite3")
    memory.remember("lesson", value, importance=0.8, source="test")
    memory.set_meta("boot_count", "7")
    memory.set_meta("genesis_initialized", "1")
    memory.set_meta("next_wake_at", WAKE_AT)
    memory.set_meta("last_sleep_seconds", "123.000000")
    memory.create_goal(
        "Preserve the test continuity goal",
        "This goal must survive migration to a fresh machine.",
        priority=0.9,
        source="test",
    )
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    chronicle.append("BOOT", {"identity": "ELIA"})
    chronicle.append("CYCLE", {"value": value})
    workspace = state_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "note.txt").write_text(value, encoding="utf-8")


def test_checkpoint_roundtrip_to_fresh_state(tmp_path: Path) -> None:
    original = tmp_path / "source" / ".elia"
    seed_state(original)
    checkpoint = tmp_path / "genesis.eliacp"

    exported = CheckpointManager(original, "ELIA", KEY).export(checkpoint)
    assert checkpoint.exists()
    assert exported.counter == 1

    fresh = tmp_path / "fresh" / ".elia"
    restored = CheckpointManager(fresh, "ELIA", KEY).restore(
        checkpoint, expected_digest=exported.digest
    )

    assert restored.digest == exported.digest
    assert Chronicle(fresh / "chronicle.jsonl").verify() == (True, None)
    assert (fresh / "workspace" / "note.txt").read_text(encoding="utf-8") == "alpha"
    memory = MemoryStore(fresh / "memory.sqlite3")
    assert memory.get_meta("boot_count") == "7"
    assert memory.get_meta("checkpoint_digest") == exported.digest
    assert memory.get_meta("restored_from_checkpoint") == exported.digest
    assert memory.get_meta("next_wake_at") == WAKE_AT
    assert memory.get_meta("last_sleep_seconds") == "123.000000"
    goals = memory.active_goals()
    assert len(goals) == 1
    assert goals[0].title == "Preserve the test continuity goal"
    assert goals[0].priority == 0.9


def test_checkpoint_export_rejects_destination_inside_state(tmp_path: Path) -> None:
    state = tmp_path / ".elia"
    seed_state(state)
    manager = CheckpointManager(state, "ELIA", KEY)

    with pytest.raises(CheckpointError, match="outside the replaceable organism state"):
        manager.export(state / "memory.sqlite3")

    assert MemoryStore(state / "memory.sqlite3").get_meta("genesis_initialized") == "1"


@pytest.mark.parametrize("target_kind", ["publish_journal", "writer_lock", "owner_sidecar"])
def test_checkpoint_export_rejects_kernel_control_destinations(
    tmp_path: Path,
    target_kind: str,
) -> None:
    state = tmp_path / ".elia"
    seed_state(state)
    manager = CheckpointManager(state, "ELIA", KEY)
    targets = {
        "publish_journal": manager.publish_journal_path,
        "writer_lock": state_writer_lock_path(state),
        "owner_sidecar": state.parent / f".{state.name}.owner-control.json",
    }

    with pytest.raises(CheckpointError, match="kernel-control storage"):
        manager.export(targets[target_kind])

    assert not manager.publish_journal_path.exists()
    assert MemoryStore(state / "memory.sqlite3").get_meta("checkpoint_counter", "0") == "0"
    exported = manager.export(tmp_path / f"safe-{target_kind}.eliacp")
    assert exported.counter == 1


def test_checkpoint_roundtrip_preserves_empty_directories_and_modes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / ".elia"
    seed_state(source)
    workspace = source / "workspace"
    empty = workspace / "empty" / "nested"
    empty.mkdir(parents=True)
    executable = workspace / "run.sh"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o750)
    empty.chmod(0o710)
    checkpoint = tmp_path / "modes.eliacp"

    exported = CheckpointManager(source, "ELIA", KEY).export(checkpoint)
    target = tmp_path / "target" / ".elia"
    CheckpointManager(target, "ELIA", KEY).restore(
        checkpoint, expected_digest=exported.digest
    )

    assert (target / "workspace" / "empty" / "nested").is_dir()
    assert stat.S_IMODE((target / "workspace" / "run.sh").stat().st_mode) == 0o750
    assert stat.S_IMODE(
        (target / "workspace" / "empty" / "nested").stat().st_mode
    ) == 0o710


def test_checkpoint_rejects_workspace_hardlink_before_archive_copy(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".elia"
    seed_state(state)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"one inode")
    os.link(outside, state / "workspace" / "linked.bin")
    checkpoint = tmp_path / "hardlink.eliacp"

    with pytest.raises(CheckpointError, match="hard-linked file"):
        CheckpointManager(state, "ELIA", KEY).export(checkpoint)

    assert not checkpoint.exists()


def test_checkpoint_wrong_key_is_rejected(tmp_path: Path) -> None:
    state = tmp_path / ".elia"
    seed_state(state)
    checkpoint = tmp_path / "genesis.eliacp"
    CheckpointManager(state, "ELIA", KEY).export(checkpoint)

    with pytest.raises(CheckpointAuthenticationError):
        CheckpointManager(tmp_path / "fresh", "ELIA", b"a-different-long-secret-key").inspect(checkpoint)


def test_checkpoint_payload_tampering_is_rejected(tmp_path: Path) -> None:
    state = tmp_path / ".elia"
    seed_state(state)
    checkpoint = tmp_path / "genesis.eliacp"
    CheckpointManager(state, "ELIA", KEY).export(checkpoint)

    tampered = tmp_path / "tampered.eliacp"
    with zipfile.ZipFile(checkpoint, "r") as source, zipfile.ZipFile(tampered, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "state/workspace/note.txt":
                data = b"tampered"
            target.writestr(item, data)

    with pytest.raises(CheckpointError, match="(size|hash) mismatch"):
        CheckpointManager(tmp_path / "fresh", "ELIA", KEY).inspect(tampered)


def test_legacy_and_tampered_local_anchors_fail_closed(tmp_path: Path) -> None:
    legacy_state = tmp_path / "legacy" / ".elia"
    seed_state(legacy_state)
    legacy_memory = MemoryStore(legacy_state / "memory.sqlite3")
    legacy_memory.set_meta("checkpoint_counter", "1")
    legacy_memory.set_meta("checkpoint_digest", "a" * 64)
    (legacy_state / "checkpoint.anchor.json").write_text(
        json.dumps(
            {
                "counter": 1,
                "digest": "a" * 64,
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CheckpointAuthenticationError, match="legacy"):
        CheckpointManager(legacy_state, "ELIA", KEY).export(
            tmp_path / "legacy.eliacp"
        )

    state = tmp_path / "authenticated" / ".elia"
    seed_state(state)
    manager = CheckpointManager(state, "ELIA", KEY)
    manager.export(tmp_path / "authenticated.eliacp")
    anchor = json.loads(manager.anchor_path.read_text(encoding="utf-8"))
    anchor["counter"] = 0
    manager.anchor_path.write_text(json.dumps(anchor), encoding="utf-8")
    with pytest.raises(CheckpointAuthenticationError, match="authentication failed"):
        manager.export(tmp_path / "tampered-anchor.eliacp")


def test_older_checkpoint_is_rejected_after_newer_anchor(tmp_path: Path) -> None:
    state = tmp_path / ".elia"
    seed_state(state, "one")
    manager = CheckpointManager(state, "ELIA", KEY)
    first_path = tmp_path / "first.eliacp"
    first = manager.export(first_path)

    MemoryStore(state / "memory.sqlite3").remember("lesson", "two", source="test")
    Chronicle(state / "chronicle.jsonl").append("CYCLE", {"value": "two"})
    second_path = tmp_path / "second.eliacp"
    second = manager.export(second_path)
    assert second.counter == first.counter + 1

    with pytest.raises(CheckpointRollbackError):
        manager.restore(first_path)


def test_fresh_machine_expected_digest_rejects_old_checkpoint(tmp_path: Path) -> None:
    state = tmp_path / "source" / ".elia"
    seed_state(state)
    manager = CheckpointManager(state, "ELIA", KEY)
    old_path = tmp_path / "old.eliacp"
    manager.export(old_path)

    Chronicle(state / "chronicle.jsonl").append("CYCLE", {"value": "new"})
    new_path = tmp_path / "new.eliacp"
    new = manager.export(new_path)

    fresh = tmp_path / "fresh" / ".elia"
    with pytest.raises(CheckpointRollbackError):
        CheckpointManager(fresh, "ELIA", KEY).restore(old_path, expected_digest=new.digest)
    assert not fresh.exists()


def test_fresh_machine_restore_requires_out_of_band_trusted_digest(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / ".elia"
    seed_state(source)
    checkpoint = tmp_path / "checkpoint.eliacp"
    CheckpointManager(source, "ELIA", KEY).export(checkpoint)

    fresh = tmp_path / "fresh" / ".elia"
    with pytest.raises(CheckpointRollbackError, match="fresh-machine"):
        CheckpointManager(fresh, "ELIA", KEY).restore(checkpoint)
    assert not fresh.exists()


def test_restore_rejects_skipped_predecessor_even_with_valid_hmac(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / ".elia"
    seed_state(source, "one")
    source_manager = CheckpointManager(source, "ELIA", KEY)
    first = source_manager.export(tmp_path / "first.eliacp")
    Chronicle(source / "chronicle.jsonl").append("CYCLE", {"value": "two"})
    source_manager.export(tmp_path / "second.eliacp")
    Chronicle(source / "chronicle.jsonl").append("CYCLE", {"value": "three"})
    third = source_manager.export(tmp_path / "third.eliacp")

    target = tmp_path / "target" / ".elia"
    target_manager = CheckpointManager(target, "ELIA", KEY)
    target_manager.restore(tmp_path / "first.eliacp", expected_digest=first.digest)
    with pytest.raises(CheckpointRollbackError, match="skips trusted predecessors"):
        target_manager.restore(tmp_path / "third.eliacp", expected_digest=third.digest)


def test_publish_journal_completes_after_anchor_write_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / ".elia"
    seed_state(state)
    first_path = tmp_path / "first.eliacp"
    manager = CheckpointManager(state, "ELIA", KEY)

    def fail_anchor(**_kwargs) -> None:
        raise OSError("simulated power loss before anchor publication")

    monkeypatch.setattr(manager, "_write_anchor", fail_anchor)
    with pytest.raises(OSError, match="simulated power loss"):
        manager.export(first_path)
    assert first_path.is_file()
    assert manager.publish_journal_path.is_file()

    recovery_manager = CheckpointManager(state, "ELIA", KEY)
    recovered_first = recovery_manager.export(first_path)
    assert recovered_first.counter == 1
    assert not manager.publish_journal_path.exists()
    recovered = recovery_manager.export(tmp_path / "second.eliacp")
    assert recovered.counter == 2


def test_restore_fault_before_durable_new_marker_recovers_old_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source" / ".elia"
    seed_state(source, "replacement")
    checkpoint = tmp_path / "replacement.eliacp"
    exported = CheckpointManager(source, "ELIA", KEY).export(checkpoint)

    target = tmp_path / "target" / ".elia"
    seed_state(target, "accepted-before")
    manager = CheckpointManager(target, "ELIA", KEY)
    original_write = manager._write_restore_journal

    def fail_new_marker(payload) -> None:
        if payload.get("status") == "new_moved":
            raise OSError("simulated crash before durable new-state marker")
        original_write(payload)

    monkeypatch.setattr(manager, "_write_restore_journal", fail_new_marker)
    with pytest.raises(OSError, match="durable new-state marker"):
        manager.restore(checkpoint, expected_digest=exported.digest)

    assert (
        target / "workspace" / "note.txt"
    ).read_text(encoding="utf-8") == "accepted-before"
    assert not manager.control_root.joinpath("restore.json").exists()


def test_concurrent_exports_are_globally_serialized(tmp_path: Path) -> None:
    state = tmp_path / ".elia"
    seed_state(state)
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_concurrent_export,
            args=(str(state), str(tmp_path / f"checkpoint-{index}.eliacp"), queue),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    results = [queue.get(timeout=2), queue.get(timeout=2)]
    assert all(item[0] == "ok" for item in results), results
    assert sorted(int(item[1]) for item in results) == [1, 2]


def test_restore_cannot_resurrect_consumed_human_approval(tmp_path: Path) -> None:
    state = tmp_path / ".elia"
    seed_state(state)
    mandate = OwnerMandate(
        schema_version=1,
        precedence=("owner", "continuity"),
        require_external_lease=False,
        approval_required_actions=("submit_work",),
        default_lease_hours=1.0,
        fingerprint="f" * 64,
    )
    owner = OwnerControl(state / "memory.sqlite3", mandate)
    arguments = {"work_item_id": 17}
    owner.approve_once(
        "submit_work",
        arguments,
        approved_by="owner",
        evidence="approved once",
    )
    manager = CheckpointManager(state, "ELIA", KEY)
    checkpoint = tmp_path / "with-unconsumed-approval.eliacp"
    exported = manager.export(checkpoint)

    owner.assert_external_authorized("submit_work", arguments)
    manager.restore(checkpoint, expected_digest=exported.digest)

    restored_owner = OwnerControl(state / "memory.sqlite3", mandate)
    with pytest.raises(HumanApprovalRequired):
        restored_owner.assert_external_authorized("submit_work", arguments)


def test_failed_restore_preserves_existing_state(tmp_path: Path) -> None:
    source = tmp_path / "source" / ".elia"
    seed_state(source, "source")
    checkpoint = tmp_path / "checkpoint.eliacp"
    CheckpointManager(source, "ELIA", KEY).export(checkpoint)

    target = tmp_path / "target" / ".elia"
    seed_state(target, "target")
    before = (target / "workspace" / "note.txt").read_text(encoding="utf-8")

    with pytest.raises(CheckpointAuthenticationError):
        CheckpointManager(target, "ELIA", b"wrong-key-but-long-enough").restore(checkpoint)

    assert (target / "workspace" / "note.txt").read_text(encoding="utf-8") == before
    assert Chronicle(target / "chronicle.jsonl").verify() == (True, None)
