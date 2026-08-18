from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from elia.observations import ObservationStore


def test_sensorium_rejects_stored_payload_tamper(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    store = ObservationStore(database)
    observation = store.record(
        source_kind="test",
        source_ref="probe",
        payload={"status": "accepted"},
    )
    assert store.get(observation.id).payload == {"status": "accepted"}

    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE observations SET payload_json=? WHERE id=?",
            ('{"status":"tampered"}', observation.id),
        )

    with pytest.raises(RuntimeError, match="stored payload digest mismatch"):
        store.get(observation.id)


def test_sensorium_rejects_original_digest_marker_tamper_even_if_stored_digest_is_rehashed(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.sqlite3"
    store = ObservationStore(database)
    first = store.record(
        source_kind="test",
        source_ref="old",
        payload={"secret": "A" * 4000},
    )
    store.record(source_kind="test", source_ref="new-1", payload={"v": 1})
    store.record(source_kind="test", source_ref="new-2", payload={"v": 2})
    assert store.compact_aged_payloads(keep_recent=2, batch=10) == 1

    with sqlite3.connect(database) as conn:
        row = conn.execute(
            "SELECT payload_json FROM observations WHERE id=?", (first.id,)
        ).fetchone()
        tampered = str(row[0]).replace(first.payload_sha256, "f" * 64)
        from hashlib import sha256

        conn.execute(
            "UPDATE observations SET payload_json=?, stored_payload_sha256=? WHERE id=?",
            (tampered, sha256(tampered.encode("utf-8")).hexdigest(), first.id),
        )

    with pytest.raises(RuntimeError, match="original digest marker mismatch"):
        store.get(first.id)
