from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from elia.chronicle import Chronicle
from elia.transition_kernel import AcceptedTransitionGuard


def _database(path: Path) -> Path:
    database = path / "memory.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO kv(key,value) VALUES ('state','accepted')")
        # Minimal safety tables exercise generic preservation without importing MCP.
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


def test_process_death_journal_is_recovered_before_next_boot(tmp_path: Path) -> None:
    state_dir = tmp_path / ".elia"
    state_dir.mkdir()
    database = _database(state_dir)
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    chronicle.append("BOOT", {"accepted": True})
    accepted_head = chronicle.head()

    # Simulate SIGKILL/power loss: enter the barrier, commit dirty state, then release
    # the OS lock without running __exit__ or cleanup.
    guard = AcceptedTransitionGuard(state_dir, chronicle)
    guard.__enter__()
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE kv SET value='dirty-after-crash' WHERE key='state'")
    chronicle.append("CYCLE", {"would_have_been": "unaccepted"})
    guard._release()

    assert _value(database) == "dirty-after-crash"
    assert (state_dir / "transition-kernel" / "active.json").is_file()

    recovery = AcceptedTransitionGuard.recover_incomplete(state_dir, chronicle)
    assert recovery.recovered is True
    assert _value(database) == "accepted"
    assert chronicle.head() == accepted_head
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
