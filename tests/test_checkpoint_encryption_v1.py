from __future__ import annotations

import base64
from pathlib import Path
import zipfile

import pytest

from elia.checkpoint import (
    CheckpointEncryptionError,
    CheckpointManager,
    ENVELOPE_MAGIC,
)
from elia.chronicle import Chronicle
from elia.memory import MemoryStore


AUTH_KEY = b"genesis-test-key-32-bytes-long!!"
ENC_KEY = b"e" * 32


def seed_state(state_dir: Path) -> None:
    memory = MemoryStore(state_dir / "memory.sqlite3")
    memory.remember("lesson", "private continuity", importance=1.0, source="test")
    memory.set_meta("boot_count", "1")
    memory.set_meta("genesis_initialized", "1")
    Chronicle(state_dir / "chronicle.jsonl").append("BOOT", {"identity": "ELIA"})
    workspace = state_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "secret-note.txt").write_text("private payload", encoding="utf-8")


def test_encrypted_checkpoint_roundtrip_hides_zip_payload(tmp_path: Path) -> None:
    source = tmp_path / "source" / ".elia"
    seed_state(source)
    checkpoint = tmp_path / "encrypted.eliacp"

    exported = CheckpointManager(
        source,
        "ELIA",
        AUTH_KEY,
        encryption_key=ENC_KEY,
        require_encryption=True,
    ).export(checkpoint)

    assert checkpoint.read_bytes().startswith(ENVELOPE_MAGIC)
    assert not zipfile.is_zipfile(checkpoint)
    assert b"private payload" not in checkpoint.read_bytes()

    target = tmp_path / "target" / ".elia"
    restored = CheckpointManager(
        target,
        "ELIA",
        AUTH_KEY,
        encryption_key=ENC_KEY,
        require_encryption=True,
    ).restore(checkpoint, expected_digest=exported.digest)

    assert restored.digest == exported.digest
    assert (target / "workspace" / "secret-note.txt").read_text(encoding="utf-8") == "private payload"
    assert Chronicle(target / "chronicle.jsonl").verify() == (True, None)


def test_wrong_encryption_key_is_rejected_before_archive_parse(tmp_path: Path) -> None:
    source = tmp_path / "source" / ".elia"
    seed_state(source)
    checkpoint = tmp_path / "encrypted.eliacp"
    CheckpointManager(
        source,
        "ELIA",
        AUTH_KEY,
        encryption_key=ENC_KEY,
        require_encryption=True,
    ).export(checkpoint)

    with pytest.raises(CheckpointEncryptionError, match="envelope authentication failed"):
        CheckpointManager(
            tmp_path / "target" / ".elia",
            "ELIA",
            AUTH_KEY,
            encryption_key=b"x" * 32,
            require_encryption=True,
        ).inspect(checkpoint)


def test_strict_encrypted_mode_rejects_legacy_plaintext_checkpoint(tmp_path: Path) -> None:
    source = tmp_path / "source" / ".elia"
    seed_state(source)
    checkpoint = tmp_path / "legacy.eliacp"
    CheckpointManager(source, "ELIA", AUTH_KEY).export(checkpoint)
    assert zipfile.is_zipfile(checkpoint)

    with pytest.raises(CheckpointEncryptionError, match="plaintext legacy checkpoint rejected"):
        CheckpointManager(
            tmp_path / "target" / ".elia",
            "ELIA",
            AUTH_KEY,
            encryption_key=ENC_KEY,
            require_encryption=True,
        ).inspect(checkpoint)


def test_encryption_key_can_be_loaded_from_strict_base64_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / ".elia"
    seed_state(source)
    checkpoint = tmp_path / "env-encrypted.eliacp"
    monkeypatch.setenv(
        "ELIA_CHECKPOINT_ENCRYPTION_KEY",
        base64.b64encode(ENC_KEY).decode("ascii"),
    )
    monkeypatch.setenv("ELIA_CHECKPOINT_REQUIRE_ENCRYPTION", "1")

    exported = CheckpointManager(source, "ELIA", AUTH_KEY).export(checkpoint)
    inspected = CheckpointManager(
        tmp_path / "inspect" / ".elia", "ELIA", AUTH_KEY
    ).inspect(checkpoint, expected_digest=exported.digest)

    assert inspected.digest == exported.digest
    assert checkpoint.read_bytes().startswith(ENVELOPE_MAGIC)
