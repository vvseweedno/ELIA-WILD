from __future__ import annotations

from pathlib import Path

import pytest

from elia.evolution import BodyRevisionStore, RevisionGate
from elia.verification import VerificationRegistry


VERIFY_KEY = b"body-revision-verifier-key-32bytes-min!"


def _registry() -> VerificationRegistry:
    return VerificationRegistry({"ci:controlled-ablation": VERIFY_KEY})


def _evaluation_receipt(
    registry: VerificationRegistry,
    *,
    revision_id: int,
    tests_passed: bool,
    organism_healthy: bool,
    continuity_status: str,
    metrics: dict,
    evidence: str,
):
    claim = BodyRevisionStore.evaluation_claim(
        revision_id=revision_id,
        tests_passed=tests_passed,
        organism_healthy=organism_healthy,
        continuity_status=continuity_status,
        metrics=metrics,
    )
    return registry.issue("ci:controlled-ablation", claim=claim, evidence=evidence)


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


def test_model_like_proposal_cannot_self_validate_without_signed_evaluator(tmp_path: Path) -> None:
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

    with pytest.raises(ValueError, match="evidence"):
        store.evaluate(
            revision_id,
            tests_passed=True,
            organism_healthy=True,
            continuity_status="continuous",
            metrics={},
            evidence="",
            evaluator_authority="model:self",
        )
    assert store.get(revision_id).status == "proposed"

    with pytest.raises(ValueError, match="signed VerificationReceipt"):
        store.evaluate(
            revision_id,
            tests_passed=True,
            organism_healthy=True,
            continuity_status="continuous",
            metrics={},
            evidence="model claims tests passed",
            evaluator_authority="model:self",
        )
    assert store.get(revision_id).status == "proposed"


def test_signed_evaluation_can_validate_revision_and_tampering_is_rejected(tmp_path: Path) -> None:
    registry = _registry()
    store = BodyRevisionStore(tmp_path / "memory.sqlite3", verification_registry=registry)
    revision_id = store.create(
        title="Measured memory revision",
        hypothesis="Candidate improves retention without regression.",
        target_organs=["persistent_memory"],
        proposed_change="Run candidate only in isolated test body.",
        expected_metrics={"retention": {"direction": "higher"}},
        regression_plan="Run deterministic regression suite.",
        rollback_plan="Discard candidate branch on failure.",
    )
    metrics = {
        "retention": {
            "baseline": 0.80,
            "candidate": 0.85,
            "direction": "higher",
            "min_delta": 0.01,
        }
    }
    evidence = "CI run 123 + signed benchmark artifact digest"
    receipt = _evaluation_receipt(
        registry,
        revision_id=revision_id,
        tests_passed=True,
        organism_healthy=True,
        continuity_status="continuous",
        metrics=metrics,
        evidence=evidence,
    )
    updated, report = store.evaluate(
        revision_id,
        tests_passed=True,
        organism_healthy=True,
        continuity_status="continuous",
        metrics=metrics,
        evidence=evidence,
        verification_receipt=receipt,
    )
    assert report.accepted is True
    assert updated.status == "validated"
    event = store.events(revision_id)[-1]
    assert event["payload"]["evaluator_authority"] == "ci:controlled-ablation"
    assert event["payload"]["verification_receipt"]["signature"] == receipt.signature

    second_id = store.create(
        title="Tamper test",
        hypothesis="Receipt must bind the exact metrics.",
        target_organs=["persistent_memory"],
        proposed_change="No deployment.",
        expected_metrics={},
        regression_plan="Controlled test.",
        rollback_plan="Discard candidate.",
    )
    good_metrics = {"score": {"baseline": 1.0, "candidate": 1.1, "direction": "higher"}}
    receipt2 = _evaluation_receipt(
        registry,
        revision_id=second_id,
        tests_passed=True,
        organism_healthy=True,
        continuity_status="continuous",
        metrics=good_metrics,
        evidence="artifact digest X",
    )
    with pytest.raises(PermissionError, match="claim digest mismatch"):
        store.evaluate(
            second_id,
            tests_passed=True,
            organism_healthy=True,
            continuity_status="continuous",
            metrics={"score": {"baseline": 1.0, "candidate": 9.9, "direction": "higher"}},
            evidence="artifact digest X",
            verification_receipt=receipt2,
        )


def test_failed_metric_rejects_revision_and_preserves_event_history(tmp_path: Path) -> None:
    registry = _registry()
    store = BodyRevisionStore(tmp_path / "memory.sqlite3", verification_registry=registry)
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
    metrics = {
        "score": {"baseline": 0.8, "candidate": 0.79, "direction": "higher", "min_delta": 0.0}
    }
    evidence = "same-seed controlled run"
    receipt = _evaluation_receipt(
        registry,
        revision_id=revision_id,
        tests_passed=True,
        organism_healthy=True,
        continuity_status="continuous",
        metrics=metrics,
        evidence=evidence,
    )
    updated, report = store.evaluate(
        revision_id,
        tests_passed=True,
        organism_healthy=True,
        continuity_status="continuous",
        metrics=metrics,
        evidence=evidence,
        verification_receipt=receipt,
    )
    assert report.accepted is False
    assert updated.status == "rejected"
    assert [item["kind"] for item in store.events(revision_id)] == ["proposed", "testing", "rejected"]
