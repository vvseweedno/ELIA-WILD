from __future__ import annotations

from pathlib import Path

import pytest

from elia.verification import VerificationRegistry
from elia.world_model import WorldModelStore


VERIFY_KEY = b"world-model-test-key-32-bytes!!"


def _registry() -> VerificationRegistry:
    return VerificationRegistry({"test:world": VERIFY_KEY})


def test_model_belief_is_capped_and_cannot_self_verify(tmp_path: Path) -> None:
    registry = _registry()
    store = WorldModelStore(tmp_path / "memory.sqlite3", verification_registry=registry)
    belief = store.propose(
        domain="web",
        subject="example.org",
        predicate="offers",
        object={"thing": "public documentation"},
        confidence=0.99,
        evidence="Observed page text in observation #7.",
        source="brain",
        observation_id=7,
    )
    assert belief.status == "hypothesis"
    assert belief.confidence == 0.75

    with pytest.raises(ValueError, match="hypothesis/supported/disputed"):
        store.revise_from_model(
            belief.id,
            status="verified",
            confidence=1.0,
            evidence="I am sure.",
        )

    evidence = "Trusted adapter receipt/observation establishes the claim."
    claim = WorldModelStore.adjudication_claim(
        belief,
        status="verified",
        confidence=0.98,
        observation_id=8,
    )
    receipt = registry.issue(
        "test:world",
        claim=claim,
        evidence=evidence,
        nonce="world-adjudication-1",
    )
    verified = store.adjudicate(
        belief.id,
        status="verified",
        confidence=0.98,
        evidence=evidence,
        verification_receipt=receipt,
        observation_id=8,
    )
    assert verified.status == "verified"
    assert verified.confidence == 0.98

    with pytest.raises(PermissionError, match="already consumed"):
        store.adjudicate(
            belief.id,
            status="verified",
            confidence=0.98,
            evidence=evidence,
            verification_receipt=receipt,
            observation_id=8,
        )


def test_plain_authority_string_cannot_promote_world_belief(tmp_path: Path) -> None:
    store = WorldModelStore(tmp_path / "memory.sqlite3")
    belief = store.propose(
        domain="runtime",
        subject="service-A",
        predicate="status",
        object="healthy",
        confidence=0.5,
        evidence="local probe",
    )
    with pytest.raises(ValueError, match="signed VerificationReceipt"):
        store.adjudicate(
            belief.id,
            status="verified",
            confidence=0.99,
            evidence="caller says this is trusted",
            authority="test-verifier",
        )
    assert store.get(belief.id).status == "hypothesis"


def test_duplicate_belief_reuses_identity_and_conflicting_object_is_visible(tmp_path: Path) -> None:
    store = WorldModelStore(tmp_path / "memory.sqlite3")
    first = store.propose(
        domain="runtime",
        subject="service-A",
        predicate="status",
        object="healthy",
        confidence=0.4,
        evidence="probe one",
    )
    repeated = store.propose(
        domain="runtime",
        subject="service-A",
        predicate="status",
        object="healthy",
        confidence=0.6,
        evidence="probe two",
    )
    assert repeated.id == first.id
    assert repeated.confidence == 0.6

    other = store.propose(
        domain="runtime",
        subject="service-A",
        predicate="status",
        object="degraded",
        confidence=0.5,
        evidence="probe three disagrees",
    )
    conflicts = store.contradictions("service-A", "status")
    assert {item.id for item in conflicts} == {first.id, other.id}
    snapshot = store.snapshot()
    assert snapshot["contradictions"][0]["subject"] == "service-A"
