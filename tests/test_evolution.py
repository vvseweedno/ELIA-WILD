from __future__ import annotations

from pathlib import Path

from elia.evolution import BodyRevisionStore, RevisionGate


def test_revision_gate_requires_tests_organism_and_continuity() -> None:
    gate = RevisionGate()
    accepted = gate.evaluate(
        tests_passed=True,
        organism_healthy=True,
        continuity_status="continuous",
        metrics={
            "quality": {"baseline": 0.5, "candidate": 0.6, "direction": "higher", "min_delta": 0.05},
            "runtime": {"baseline": 10.0, "candidate": 9.0, "direction": "lower", "min_delta": 0.5},
        },
    )
    assert accepted.accepted is True
    assert all(accepted.metric_results.values())

    rejected = gate.evaluate(
        tests_passed=False,
        organism_healthy=True,
        continuity_status="continuous",
        metrics={},
    )
    assert rejected.accepted is False
    assert "regression tests did not pass" in rejected.reasons


def test_model_like_proposal_cannot_self_validate_without_evidence(tmp_path: Path) -> None:
    store = BodyRevisionStore(tmp_path / "memory.sqlite3")
    revision_id = store.create(
        title="Try a new memory backend",
        hypothesis="The candidate may improve retention per compute unit.",
        target_organs=["persistent_memory", "memory_backends"],
        proposed_change="Evaluate the candidate in a disposable body branch.",
        expected_metrics={"retention": {"direction": "higher", "min_delta": 0.01}},
        regression_plan="Run continuity, memory and authority regression suites.",
        rollback_plan="Keep current main body and checkpoint unchanged until validation.",
        source="brain",
    )
    assert store.get(revision_id).status == "proposed"

    try:
        store.evaluate(
            revision_id,
            tests_passed=True,
            organism_healthy=True,
            continuity_status="continuous",
            metrics={},
            evidence="",
            evaluator_authority="",
        )
    except ValueError as exc:
        assert "evidence" in str(exc)
    else:
        raise AssertionError("revision self-validation unexpectedly succeeded")
    assert store.get(revision_id).status == "proposed"


def test_failed_metric_rejects_revision_and_preserves_event_history(tmp_path: Path) -> None:
    store = BodyRevisionStore(tmp_path / "memory.sqlite3")
    revision_id = store.create(
        title="Omega ablation",
        hypothesis="ContextAnchor may improve a declared metric.",
        target_organs=["omega_adapters"],
        proposed_change="Enable only in an isolated experiment.",
        expected_metrics={"score": {"direction": "higher"}},
        regression_plan="Run baseline and candidate under the same budget.",
        rollback_plan="Discard the candidate branch.",
    )
    store.start_testing(revision_id, evidence="candidate branch prepared")
    updated, report = store.evaluate(
        revision_id,
        tests_passed=True,
        organism_healthy=True,
        continuity_status="continuous",
        metrics={
            "score": {"baseline": 0.8, "candidate": 0.79, "direction": "higher", "min_delta": 0.0}
        },
        evidence="same-seed controlled run",
        evaluator_authority="ci:controlled-ablation",
    )
    assert report.accepted is False
    assert updated.status == "rejected"
    assert [item["kind"] for item in store.events(revision_id)] == ["proposed", "testing", "rejected"]
