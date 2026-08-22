from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from elia.observations import ObservationStore, _canonical_json, _text_digest


def test_user_payload_compacted_marker_is_not_structural_state(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path / "observations.sqlite3")
    payload = {
        "_compacted": True,
        "original_sha256": "user-owned-field",
        "content": "ordinary observation payload",
    }

    observation = store.record(source_kind="test", source_ref="marker", payload=payload)

    assert observation.payload == payload
    with sqlite3.connect(store.path) as conn:
        row = conn.execute(
            "SELECT is_compacted FROM observations WHERE id=?", (observation.id,)
        ).fetchone()
    assert row == (0,)


def test_compaction_is_idempotent_via_structural_flag(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path / "observations.sqlite3")
    for index in range(5):
        store.record(source_kind="test", source_ref=str(index), payload={"index": index})

    assert store.compact_aged_payloads(keep_recent=2, batch=32) == 3
    assert store.compact_aged_payloads(keep_recent=2, batch=32) == 0

    with sqlite3.connect(store.path) as conn:
        rows = conn.execute(
            "SELECT is_compacted FROM observations ORDER BY id"
        ).fetchall()
    assert rows == [(1,), (1,), (1,), (0,), (0,)]


def test_legacy_compacted_rows_migrate_before_compaction_index(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    original_digest = "a" * 64
    compacted = _canonical_json(
        {
            "_compacted": True,
            "original_sha256": original_digest,
            "previous_stored_bytes": 123,
        }
    )
    timestamp = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                transaction_id TEXT NULL,
                source_kind TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                modality TEXT NOT NULL,
                content_type TEXT NOT NULL,
                trust REAL NOT NULL,
                success INTEGER NOT NULL,
                summary TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                stored_payload_sha256 TEXT NOT NULL DEFAULT '',
                provenance_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO observations(
                observed_at, transaction_id, source_kind, source_ref, modality,
                content_type, trust, success, summary, payload_json,
                payload_sha256, stored_payload_sha256, provenance_json
            ) VALUES (?, NULL, 'legacy', 'row', 'structured', 'application/json',
                      1.0, 1, 'legacy compacted row', ?, ?, ?, '{}')
            """,
            (timestamp, compacted, original_digest, _text_digest(compacted)),
        )

    store = ObservationStore(path)
    observation = store.get(1)

    assert observation is not None
    assert observation.payload["_compacted"] is True
    with sqlite3.connect(path) as conn:
        column_names = {
            row[1] for row in conn.execute("PRAGMA table_info(observations)").fetchall()
        }
        structural = conn.execute(
            "SELECT is_compacted FROM observations WHERE id=1"
        ).fetchone()
        indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(observations)").fetchall()
        }
    assert "is_compacted" in column_names
    assert structural == (1,)
    assert "idx_observations_compaction" in indexes
