from __future__ import annotations

from pathlib import Path

from elia.longitudinal import LongitudinalContinuityStore


def capsule(
    fingerprint: str,
    *,
    body: str = "1.0.0a3",
    backend: str = "mock",
    model: str = "mock",
    seq: int = 1,
):
    return {
        "capsule_fingerprint": fingerprint,
        "identity_fingerprint": "identity-fp",
        "branch_id": "main",
        "body_version": body,
        "brain_backend": backend,
        "model_id": model,
        "chronicle_seq": seq,
    }


def organism(architecture: str):
    return {"architecture_fingerprint": architecture}


def test_longitudinal_series_deduplicates_polling_and_counts_substrate_changes(tmp_path: Path) -> None:
    store = LongitudinalContinuityStore(tmp_path / "memory.sqlite3")
    first = store.record(
        capsule=capsule("crc-1", seq=1),
        organism=organism("arch-1"),
        comparison=None,
        healthy=True,
    )
    duplicate = store.record(
        capsule=capsule("crc-1", seq=1),
        organism=organism("arch-1"),
        comparison=None,
        healthy=True,
    )
    assert duplicate.id == first.id

    store.record(
        capsule=capsule("crc-2", body="1.0.0a4", backend="other", model="replacement", seq=2),
        organism=organism("arch-2"),
        comparison={"status": "mutated", "score": 0.93, "critical_failures": [], "warnings": ["body changed"]},
        healthy=True,
    )
    summary = store.summary()
    assert summary["observation_count"] == 2
    assert summary["transition_count"] == 1
    assert summary["mutation_count"] == 1
    assert summary["substrate_change_count"] == 1
    assert summary["broken_count"] == 0
    assert summary["min_continuity_score"] == 0.93


def test_longitudinal_series_preserves_falsification_events(tmp_path: Path) -> None:
    store = LongitudinalContinuityStore(tmp_path / "memory.sqlite3")
    store.record(
        capsule=capsule("crc-a"),
        organism=organism("arch-a"),
        comparison=None,
        healthy=True,
    )
    store.record(
        capsule=capsule("crc-b", seq=0),
        organism=organism("arch-b"),
        comparison={
            "status": "broken",
            "score": 0.2,
            "critical_failures": ["chronicle moved backward"],
            "warnings": [],
        },
        healthy=False,
    )
    summary = store.summary()
    assert summary["broken_count"] == 1
    assert summary["healthy_fraction"] == 0.5
    assert summary["falsification_events"][0]["critical_failures"] == ["chronicle moved backward"]
