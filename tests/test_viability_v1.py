from __future__ import annotations

from pathlib import Path

from elia.config import load_config
from elia.organism import OrganismManifest
from elia.viability import run_deep_viability


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_deep_viability_proves_required_contracts_wiring_and_recovery(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ELIA_STATE_DIR", str(tmp_path / "real-vitals-state"))
    config = load_config(repo_root() / "config" / "genesis.yaml")
    manifest = OrganismManifest.load()

    report = run_deep_viability(config, manifest)
    assert report.healthy is True
    assert report.runtime_class == "ContinuityKernelRuntime"
    assert report.contract_count == report.required_organ_count
    assert report.required_organ_count > 30
    assert report.persistence["ok"] is True
    assert report.persistence["verification"]["single_use_replay_blocked"] is True
    assert report.recovery["ok"] is True
    assert report.recovery["sqlite_projection_restored"] is True
    assert report.recovery["chronicle_head_restored"] is True

    probes = {item.organ_id: item for item in report.probes}
    for organ_id in (
        "identity_lineage",
        "observation_store",
        "world_model",
        "organism_state_bus",
        "sensorimotor_fabric",
        "executive_store",
        "resource_ecology_store",
        "work_port_outbox",
        "epistemic_security_boundary",
        "verification_consumption_kernel",
        "accepted_transition_guard",
        "continuity_kernel_runtime",
    ):
        assert probes[organ_id].ok is True, probes[organ_id]
