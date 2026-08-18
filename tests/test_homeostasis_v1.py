from __future__ import annotations

from collections import namedtuple
from pathlib import Path

from elia.homeostasis import HomeostasisEngine
from elia.observations import ObservationStore
from elia.state_bus import OrganismStateBus
from elia.world_model import WorldModelStore


DiskUsage = namedtuple("DiskUsage", "total used free")


def _engine(tmp_path: Path) -> tuple[HomeostasisEngine, ObservationStore, WorldModelStore, OrganismStateBus]:
    db = tmp_path / "memory.sqlite3"
    observations = ObservationStore(db)
    world = WorldModelStore(db)
    bus = OrganismStateBus(db)
    engine = HomeostasisEngine(
        tmp_path,
        observations,
        world,
        bus,
        {"capability_count": 4, "enabled": ["browser_snapshot"], "unavailable": {}},
    )
    return engine, observations, world, bus


def test_active_cycle_is_ignored_but_stale_transaction_creates_pressure(tmp_path: Path) -> None:
    engine, _, _, bus = _engine(tmp_path)
    active = bus.begin("active-cycle")
    ignored = engine.evaluate(ignore_transaction_ids={active})
    assert "state_reconciliation" not in {item.name for item in ignored.signals}

    stale = bus.begin("stale-cycle")
    pressured = engine.evaluate(ignore_transaction_ids={active})
    signal = next(item for item in pressured.signals if item.name == "state_reconciliation")
    assert signal.severity >= 0.88
    assert stale in signal.evidence["transaction_ids"]


def test_world_contradiction_creates_epistemic_pressure(tmp_path: Path) -> None:
    engine, _, world, _ = _engine(tmp_path)
    world.propose(
        domain="test",
        subject="service",
        predicate="status",
        object="healthy",
        confidence=0.6,
        evidence="probe A",
    )
    world.propose(
        domain="test",
        subject="service",
        predicate="status",
        object="degraded",
        confidence=0.6,
        evidence="probe B",
    )
    snapshot = engine.evaluate()
    signal = next(item for item in snapshot.signals if item.name == "epistemic_conflict")
    assert signal.severity > 0.0
    assert snapshot.epistemics["contradiction_count"] == 1


def test_repeated_sensor_failures_create_degradation_pressure(tmp_path: Path) -> None:
    engine, observations, _, _ = _engine(tmp_path)
    for index in range(8):
        observations.record(
            source_kind="test",
            source_ref=f"sensor-{index}",
            payload={"index": index},
            success=index >= 7,
            summary="synthetic probe",
        )
    snapshot = engine.evaluate()
    signal = next(item for item in snapshot.signals if item.name == "sensorium_degradation")
    assert snapshot.sensorium["failure_rate"] > 0.5
    assert signal.severity >= 0.55


def test_storage_pressure_is_deterministic(monkeypatch, tmp_path: Path) -> None:
    engine, _, _, _ = _engine(tmp_path)
    monkeypatch.setattr("elia.homeostasis.shutil.disk_usage", lambda _: DiskUsage(1000, 960, 40))
    snapshot = engine.evaluate()
    signal = next(item for item in snapshot.signals if item.name == "storage_survival")
    assert snapshot.mode == "critical"
    assert signal.severity == 0.98
