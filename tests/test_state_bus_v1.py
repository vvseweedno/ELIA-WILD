from __future__ import annotations

from pathlib import Path
import sqlite3

from elia.state_bus import OrganismStateBus


def test_state_bus_hash_chain_detects_tamper(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    bus = OrganismStateBus(path)
    tx = bus.begin("test")
    bus.append(tx, phase="action", kind="DO", payload={"x": 1})
    bus.commit(tx, {"ok": True})
    assert bus.verify(tx) == (True, None)

    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE organism_events SET payload_json=? WHERE transaction_id=? AND seq=2",
            ('{"x":999}', tx),
        )
    valid, error = bus.verify(tx)
    assert valid is False
    assert error is not None and "hash mismatch" in error


def test_incomplete_transaction_is_reconciled_not_silently_deleted(tmp_path: Path) -> None:
    bus = OrganismStateBus(tmp_path / "memory.sqlite3")
    tx = bus.begin("interrupted")
    bus.append(tx, phase="action", kind="STARTED", payload={})
    assert [item["transaction_id"] for item in bus.incomplete()] == [tx]
    assert bus.reconcile_incomplete("simulated reboot") == 1
    assert bus.incomplete() == []
    events = bus.events(tx)
    assert events[-1].kind == "TRANSACTION_ABORT"
    assert events[-1].payload["reason"] == "simulated reboot"
    assert bus.verify(tx) == (True, None)
