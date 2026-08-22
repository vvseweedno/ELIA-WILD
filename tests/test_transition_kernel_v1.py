from __future__ import annotations

import os
from pathlib import Path
import multiprocessing
import sqlite3
import stat

import pytest

import elia.transition_kernel as transition_kernel_module
from elia.chronicle import Chronicle
from elia.external_effects import ExternalEffectIndeterminate, ExternalEffectLedger
from elia.owner_control import OwnerControl, OwnerMandate
from elia.transition_kernel import (
    AcceptedTransitionGuard,
    StateWriterLock,
    StateWriterLockTimeout,
)


def _contend_for_writer_lock(state_dir: str, queue) -> None:
    try:
        with StateWriterLock(Path(state_dir), timeout_seconds=0.2):
            queue.put("acquired")
    except StateWriterLockTimeout:
        queue.put("timeout")


def _database(path: Path) -> Path:
    database = path / "memory.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO kv(key,value) VALUES ('state','accepted')")
        conn.execute(
            """
            CREATE TABLE work_port_intents(
                id INTEGER PRIMARY KEY,
                work_item_id INTEGER UNIQUE,
                port_name TEXT,
                idempotency_key TEXT UNIQUE,
                artifact_sha256 TEXT,
                created_at TEXT,
                updated_at TEXT,
                status TEXT,
                attempt_count INTEGER,
                last_error TEXT,
                submission_ref TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE observations(
                id INTEGER PRIMARY KEY,
                source_kind TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
    return database


def _value(database: Path) -> str:
    with sqlite3.connect(database) as conn:
        return str(conn.execute("SELECT value FROM kv WHERE key='state'").fetchone()[0])


def test_exception_rolls_back_sqlite_and_chronicle_suffix(tmp_path: Path) -> None:
    state_dir = tmp_path / ".elia"
    state_dir.mkdir()
    database = _database(state_dir)
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    chronicle.append("BOOT", {"accepted": True})
    accepted_head = chronicle.head()

    with pytest.raises(RuntimeError, match="simulated cycle failure"):
        with AcceptedTransitionGuard(state_dir, chronicle):
            with sqlite3.connect(database) as conn:
                conn.execute("UPDATE kv SET value='speculative' WHERE key='state'")
            chronicle.append("CYCLE", {"accepted": False})
            raise RuntimeError("simulated cycle failure")

    assert _value(database) == "accepted"
    assert chronicle.head() == accepted_head
    assert chronicle.verify() == (True, None)
    assert not (state_dir / "transition-kernel" / "active.json").exists()


def test_exception_rolls_back_workspace_as_part_of_accepted_state(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".elia"
    state_dir.mkdir()
    _database(state_dir)
    workspace = state_dir / "workspace"
    (workspace / "nested").mkdir(parents=True)
    (workspace / "empty").mkdir()
    (workspace / "keep.txt").write_text("accepted", encoding="utf-8")
    (workspace / "keep.txt").chmod(0o700)
    (workspace / "nested" / "old.txt").write_text("old", encoding="utf-8")
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    chronicle.append("BOOT", {"accepted": True})

    with pytest.raises(RuntimeError, match="workspace failure"):
        with AcceptedTransitionGuard(state_dir, chronicle):
            (workspace / "keep.txt").write_text("speculative", encoding="utf-8")
            (workspace / "nested" / "old.txt").unlink()
            (workspace / "new.txt").write_text("new", encoding="utf-8")
            raise RuntimeError("workspace failure")

    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "accepted"
    assert stat.S_IMODE((workspace / "keep.txt").stat().st_mode) == 0o700
    assert (workspace / "nested" / "old.txt").read_text(encoding="utf-8") == "old"
    assert (workspace / "empty").is_dir()
    assert not (workspace / "new.txt").exists()


def test_workspace_broken_symlink_fails_before_cognition_and_is_not_deleted(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".elia"
    state_dir.mkdir()
    _database(state_dir)
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    chronicle.append("BOOT", {"accepted": True})
    workspace = state_dir / "workspace"
    workspace.symlink_to("missing-workspace-target")

    with pytest.raises(RuntimeError, match="not a real directory"):
        with AcceptedTransitionGuard(state_dir, chronicle):
            pytest.fail("cognition must not start")

    assert workspace.is_symlink()
    assert os.readlink(workspace) == "missing-workspace-target"
    assert not (state_dir / "transition-kernel" / "active.json").exists()
    assert not (state_dir / "transition-kernel" / "state-before.sqlite3").exists()


def test_workspace_hardlink_is_rejected_without_snapshot_amplification(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".elia"
    state_dir.mkdir()
    database = _database(state_dir)
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    chronicle.append("BOOT", {"accepted": True})
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"shared inode")
    workspace = state_dir / "workspace"
    workspace.mkdir()
    os.link(outside, workspace / "linked.bin")

    with pytest.raises(RuntimeError, match="hard-linked file"):
        with AcceptedTransitionGuard(state_dir, chronicle):
            pytest.fail("cognition must not start")

    assert _value(database) == "accepted"
    assert not (state_dir / "transition-kernel" / "workspace-before").exists()
    assert not (state_dir / "transition-kernel" / "state-before.sqlite3").exists()


def test_workspace_size_limit_fails_before_creating_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / ".elia"
    state_dir.mkdir()
    _database(state_dir)
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    chronicle.append("BOOT", {"accepted": True})
    workspace = state_dir / "workspace"
    workspace.mkdir()
    (workspace / "too-large.bin").write_bytes(b"123456789")
    monkeypatch.setattr(transition_kernel_module, "MAX_WORKSPACE_FILE_BYTES", 8)

    with pytest.raises(RuntimeError, match="size limit"):
        with AcceptedTransitionGuard(state_dir, chronicle):
            pytest.fail("cognition must not start")

    assert not (state_dir / "transition-kernel" / "workspace-before").exists()
    assert not (state_dir / "transition-kernel" / "state-before.sqlite3").exists()


def test_workspace_mutation_during_copy_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / ".elia"
    state_dir.mkdir()
    _database(state_dir)
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    chronicle.append("BOOT", {"accepted": True})
    workspace = state_dir / "workspace"
    workspace.mkdir()
    source = workspace / "racing.bin"
    source.write_bytes(b"accepted")
    real_read = os.read
    matching_reads = 0

    def racing_read(descriptor: int, size: int) -> bytes:
        nonlocal matching_reads
        chunk = real_read(descriptor, size)
        try:
            opened = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        except OSError:
            return chunk
        if opened == source:
            matching_reads += 1
            if matching_reads == 3:
                with source.open("ab") as handle:
                    handle.write(b"-external-mutation")
        return chunk

    monkeypatch.setattr(transition_kernel_module.os, "read", racing_read)
    with pytest.raises(RuntimeError, match="(changed|byte limits)"):
        with AcceptedTransitionGuard(state_dir, chronicle):
            pytest.fail("cognition must not start")

    assert not (state_dir / "transition-kernel" / "workspace-before").exists()
    assert not (state_dir / "transition-kernel" / "state-before.sqlite3").exists()


def test_state_writer_lock_is_cross_process_and_outside_replaceable_state(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".elia"
    state_dir.mkdir()
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    with StateWriterLock(state_dir):
        process = context.Process(
            target=_contend_for_writer_lock,
            args=(str(state_dir), queue),
        )
        process.start()
        process.join(timeout=3)
    assert process.exitcode == 0
    assert queue.get(timeout=1) == "timeout"
    assert not (state_dir / "transition.lock").exists()


def test_state_writer_lock_is_reentrant_only_within_owning_thread(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".elia"
    state_dir.mkdir()
    with StateWriterLock(state_dir):
        with StateWriterLock(state_dir, timeout_seconds=0):
            assert True


def test_writer_release_and_accept_invariants_are_explicit_not_asserts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / ".elia"
    state_dir.mkdir()
    _database(state_dir)
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    chronicle.append("BOOT", {"accepted": True})

    lock = StateWriterLock(state_dir)
    lock.acquire()
    real_fcntl = transition_kernel_module.fcntl
    monkeypatch.setattr(transition_kernel_module, "fcntl", None)
    with pytest.raises(RuntimeError, match="without fcntl"):
        lock.release()
    monkeypatch.setattr(transition_kernel_module, "fcntl", real_fcntl)
    lock.release()

    guard = AcceptedTransitionGuard(state_dir, chronicle)
    guard.__enter__()
    checkpoint = guard._checkpoint
    try:
        guard._checkpoint = None
        with pytest.raises(RuntimeError, match="checkpoint invariant"):
            guard.accept()
    finally:
        guard._checkpoint = checkpoint
        guard.__exit__(RuntimeError, RuntimeError("test cleanup"), None)


def test_process_death_journal_is_recovered_before_next_boot(tmp_path: Path) -> None:
    state_dir = tmp_path / ".elia"
    state_dir.mkdir()
    database = _database(state_dir)
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    chronicle.append("BOOT", {"accepted": True})
    accepted_head = chronicle.head()
    workspace = state_dir / "workspace"
    workspace.mkdir()
    (workspace / "accepted.txt").write_text("accepted", encoding="utf-8")

    guard = AcceptedTransitionGuard(state_dir, chronicle)
    guard.__enter__()
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE kv SET value='dirty-after-crash' WHERE key='state'")
    chronicle.append("CYCLE", {"would_have_been": "unaccepted"})
    (workspace / "accepted.txt").write_text("dirty", encoding="utf-8")
    (workspace / "orphan.txt").write_text("orphan", encoding="utf-8")
    guard._release()

    assert _value(database) == "dirty-after-crash"
    assert (state_dir / "transition-kernel" / "active.json").is_file()

    recovery = AcceptedTransitionGuard.recover_incomplete(state_dir, chronicle)
    assert recovery.recovered is True
    assert _value(database) == "accepted"
    assert chronicle.head() == accepted_head
    assert (workspace / "accepted.txt").read_text(encoding="utf-8") == "accepted"
    assert not (workspace / "orphan.txt").exists()
    assert not (state_dir / "transition-kernel" / "active.json").exists()


def test_external_outbox_evidence_survives_cognitive_rollback(tmp_path: Path) -> None:
    state_dir = tmp_path / ".elia"
    state_dir.mkdir()
    database = _database(state_dir)
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    chronicle.append("BOOT", {"accepted": True})

    with pytest.raises(RuntimeError):
        with AcceptedTransitionGuard(state_dir, chronicle):
            with sqlite3.connect(database) as conn:
                conn.execute("UPDATE kv SET value='speculative' WHERE key='state'")
                conn.execute(
                    """
                    INSERT INTO work_port_intents(
                        id, work_item_id, port_name, idempotency_key, artifact_sha256,
                        created_at, updated_at, status, attempt_count, last_error
                    ) VALUES (1, 7, 'market', 'idem-7', 'artifact-hash', 't0', 't1',
                              'indeterminate', 1, 'remote outcome unknown')
                    """
                )
                conn.execute(
                    "INSERT INTO observations(id,source_kind,source_ref,payload) "
                    "VALUES (1,'work_port','submit_work','ambiguous remote result')"
                )
            raise RuntimeError("later cognitive failure")

    assert _value(database) == "accepted"
    with sqlite3.connect(database) as conn:
        intent = conn.execute(
            "SELECT status, idempotency_key FROM work_port_intents WHERE work_item_id=7"
        ).fetchone()
        observation = conn.execute(
            "SELECT source_kind, payload FROM observations WHERE id=1"
        ).fetchone()
    assert intent == ("indeterminate", "idem-7")
    assert observation == ("work_port", "ambiguous remote result")


def _owner(database: Path) -> OwnerControl:
    mandate = OwnerMandate(
        schema_version=1,
        precedence=("owner", "continuity"),
        require_external_lease=False,
        approval_required_actions=(),
        default_lease_hours=1.0,
        fingerprint="a" * 64,
    )
    return OwnerControl(database, mandate)


def test_universal_effect_and_owner_revocation_survive_rollback(tmp_path: Path) -> None:
    state_dir = tmp_path / ".elia"
    state_dir.mkdir()
    database = _database(state_dir)
    ledger = ExternalEffectLedger(database)
    owner = _owner(database)
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    chronicle.append("BOOT", {"accepted": True})

    effect_id = ""
    with pytest.raises(RuntimeError):
        with AcceptedTransitionGuard(state_dir, chronicle):
            with sqlite3.connect(database) as conn:
                conn.execute("UPDATE kv SET value='speculative' WHERE key='state'")
            intent = ledger.prepare("browser_click", {"selector": "button[type=submit]"})
            effect_id = intent.effect_id
            ledger.mark_sending(effect_id)
            owner.revoke(reason="operator revoked while cognition was active")
            raise RuntimeError("crash after possible remote effect")

    assert _value(database) == "accepted"
    restored = ExternalEffectLedger(database).get(effect_id)
    assert restored is not None
    assert restored.status == "indeterminate"
    assert _owner(database).snapshot()["delegation_revoked"] is True


def test_successful_effect_in_rolled_back_cycle_requires_reconciliation(tmp_path: Path) -> None:
    state_dir = tmp_path / ".elia"
    state_dir.mkdir()
    database = _database(state_dir)
    ledger = ExternalEffectLedger(database)
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    chronicle.append("BOOT", {"accepted": True})
    args = {"server": "configured", "tool": "create_remote_object"}

    effect_id = ""
    with pytest.raises(RuntimeError):
        with AcceptedTransitionGuard(state_dir, chronicle):
            intent = ledger.prepare("mcp_call", args)
            effect_id = intent.effect_id
            ledger.mark_sending(effect_id)
            ledger.record_result(
                effect_id,
                ok=True,
                result={"remote_id": "created-123"},
            )
            with sqlite3.connect(database) as conn:
                conn.execute("UPDATE kv SET value='speculative-after-success' WHERE key='state'")
            raise RuntimeError("later local projection failed")

    restored_ledger = ExternalEffectLedger(database)
    restored = restored_ledger.get(effect_id)
    assert restored is not None
    assert restored.status == "indeterminate"
    assert "rolled-back accepted transition" in restored.error
    with pytest.raises(ExternalEffectIndeterminate):
        restored_ledger.prepare("mcp_call", args)
