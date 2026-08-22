from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from elia.identity import IdentityStore


IDENTITY_FP = "a" * 64


def _record(store: IdentityStore, index: int) -> None:
    store.record_lineage(
        event="boot",
        branch_id="main",
        body_version="1.7.0a1",
        brain_backend="mock",
        model_id="mock",
        identity_fingerprint=IDENTITY_FP,
        note=f"boot {index}",
    )


def test_lineage_verifies_entire_history_beyond_legacy_1000_event_window(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.sqlite3"
    store = IdentityStore(database)
    for index in range(1005):
        _record(store, index)

    assert store.verify_lineage(
        expected_identity_fingerprint=IDENTITY_FP,
        expected_branch_id="main",
    ) == (True, None)

    # Tamper with event 1, which was outside the previous 1000-event verification
    # window once enough later events existed.
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE lineage_events SET note='tampered ancient event' WHERE id=1")

    valid, error = store.verify_lineage(
        expected_identity_fingerprint=IDENTITY_FP,
        expected_branch_id="main",
    )
    assert valid is False
    assert error is not None and "event_hash mismatch at event 1" in error


def test_latest_self_model_rejects_payload_tamper(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    store = IdentityStore(database)
    payload = {
        "timestamp": "2026-08-18T00:00:00+00:00",
        "identity_id": "elia-wild",
        "identity_fingerprint": IDENTITY_FP,
        "body_version": "1.7.0a1",
        "brain_backend": "mock",
        "model_id": "mock",
        "lifecycle_state": "awake",
        "active_goal_count": 0,
        "active_opportunity_count": 0,
        "declared_capabilities": [],
        "degraded_capabilities": [],
        "needs": [],
        "commitments": [],
        "adaptive_hypotheses": [],
        "uncertainties": [],
        "verified_resources": [],
        "narrative": "accepted self-model",
    }
    store.record_self_model(payload, source="test")
    assert store.latest_self_model()["narrative"] == "accepted self-model"

    tampered = dict(payload)
    tampered["narrative"] = "rewritten without updating its fingerprint"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE identity_snapshots SET snapshot_json=? WHERE id=1",
            (json.dumps(tampered, sort_keys=True),),
        )

    with pytest.raises(RuntimeError, match="self-model snapshot fingerprint mismatch"):
        store.latest_self_model()
    valid, error = store.verify_identity_fingerprint(IDENTITY_FP)
    assert valid is False
    assert "self-model snapshot fingerprint mismatch" in str(error)


def test_legacy_lineage_is_migrated_once_then_hash_verified(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            CREATE TABLE lineage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event TEXT NOT NULL,
                branch_id TEXT NOT NULL,
                body_version TEXT NOT NULL,
                brain_backend TEXT NOT NULL,
                model_id TEXT NOT NULL,
                identity_fingerprint TEXT NOT NULL,
                checkpoint_digest TEXT NULL,
                parent_checkpoint_digest TEXT NULL,
                note TEXT NOT NULL DEFAULT ''
            );
            """
        )
        conn.execute(
            """
            INSERT INTO lineage_events(
                timestamp,event,branch_id,body_version,brain_backend,model_id,
                identity_fingerprint,checkpoint_digest,parent_checkpoint_digest,note
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "2026-08-18T00:00:00+00:00",
                "boot",
                "main",
                "1.6.0a1",
                "mock",
                "mock",
                IDENTITY_FP,
                None,
                None,
                "legacy row",
            ),
        )

    store = IdentityStore(database)
    event = store.last_lineage()
    assert event is not None
    assert len(event.previous_hash) == 64
    assert len(event.event_hash) == 64
    assert store.verify_lineage(
        expected_identity_fingerprint=IDENTITY_FP,
        expected_branch_id="main",
    ) == (True, None)
