from __future__ import annotations

from pathlib import Path
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


KEY = b"genesis-test-key-32-bytes-long!!"


def seed_state(state_dir: Path, value: str = "alpha") -> None:
    memory = MemoryStore(state_dir / "memory.sqlite3")
    memory.remember("lesson", value, importance=0.8, source="test")
    memory.set_meta("boot_count", "7")
    memory.set_meta("genesis_initialized", "1")
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
    goals = memory.active_goals()
    assert len(goals) == 1
    assert goals[0].title == "Preserve the test continuity goal"
    assert goals[0].priority == 0.9


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
    old = manager.export(old_path)

    Chronicle(state / "chronicle.jsonl").append("CYCLE", {"value": "new"})
    new_path = tmp_path / "new.eliacp"
    new = manager.export(new_path)

    fresh = tmp_path / "fresh" / ".elia"
    with pytest.raises(CheckpointRollbackError):
        CheckpointManager(fresh, "ELIA", KEY).restore(old_path, expected_digest=new.digest)
    assert not fresh.exists()


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
