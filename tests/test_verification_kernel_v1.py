from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from elia.economy import EconomyStore
from elia.evolution import BodyRevisionStore
from elia.metabolism import MetabolismStore
from elia.verification import (
    VerificationRegistry,
    consume_verified_receipt,
    ensure_receipt_ledger,
)


KEY = b"continuity-kernel-test-key-32-bytes!!"
AUTHORITY = "test:kernel"


def _registry() -> VerificationRegistry:
    return VerificationRegistry({AUTHORITY: KEY})


def test_receipt_consumption_rolls_back_with_failed_domain_transaction(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    registry = _registry()
    claim = {"type": "test", "value": 7}
    evidence = "independent evidence"
    receipt = registry.issue(AUTHORITY, claim=claim, evidence=evidence, nonce="rollback-once")

    with pytest.raises(RuntimeError, match="force rollback"):
        with sqlite3.connect(database) as conn:
            ensure_receipt_ledger(conn)
            consume_verified_receipt(
                conn,
                registry,
                receipt,
                claim=claim,
                evidence=evidence,
                purpose="test.rollback",
            )
            raise RuntimeError("force rollback")

    with sqlite3.connect(database) as conn:
        ensure_receipt_ledger(conn)
        consume_verified_receipt(
            conn,
            registry,
            receipt,
            claim=claim,
            evidence=evidence,
            purpose="test.after_rollback",
        )

    with sqlite3.connect(database) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM verification_receipt_consumptions WHERE authority=? AND nonce=?",
            (AUTHORITY, receipt.nonce),
        ).fetchone()[0]
    assert count == 1


def test_resource_receipt_replay_is_rejected_and_balance_is_single_counted(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    registry = _registry()
    economy = EconomyStore(database, verification_registry=registry)
    evidence = "payment provider event pay_001"
    claim = EconomyStore.resource_claim(
        asset="cash", unit="USD", amount=125, kind="payment", source="trusted_adapter"
    )
    receipt = registry.issue(AUTHORITY, claim=claim, evidence=evidence, nonce="payment-001")

    first_id = economy.record_resource_event(
        asset="cash",
        unit="USD",
        amount=125,
        kind="payment",
        source="trusted_adapter",
        evidence=evidence,
        verified=True,
        verification_receipt=receipt,
    )
    assert first_id > 0

    with pytest.raises(PermissionError, match="already consumed"):
        economy.record_resource_event(
            asset="cash",
            unit="USD",
            amount=125,
            kind="payment",
            source="trusted_adapter",
            evidence=evidence,
            verified=True,
            verification_receipt=receipt,
        )
    assert economy.verified_balance("cash", "USD") == 125


def test_concurrent_resource_replay_has_exactly_one_winner(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    registry = _registry()
    EconomyStore(database, verification_registry=registry)
    evidence = "payment provider event pay_race"
    claim = EconomyStore.resource_claim(
        asset="cash", unit="USD", amount=50, kind="payment", source="trusted_adapter"
    )
    receipt = registry.issue(AUTHORITY, claim=claim, evidence=evidence, nonce="race-001")

    def attempt() -> str:
        store = EconomyStore(database, verification_registry=registry)
        try:
            store.record_resource_event(
                asset="cash",
                unit="USD",
                amount=50,
                kind="payment",
                source="trusted_adapter",
                evidence=evidence,
                verified=True,
                verification_receipt=receipt,
            )
        except PermissionError:
            return "replay"
        return "accepted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _: attempt(), range(2)))
    assert outcomes == ["accepted", "replay"]
    assert EconomyStore(database).verified_balance("cash", "USD") == 50


def test_same_authority_nonce_cannot_authorize_two_different_claims(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    registry = _registry()
    store = BodyRevisionStore(database, verification_registry=registry)

    def create_revision(title: str) -> int:
        return store.create(
            title=title,
            hypothesis="candidate improves one measured property",
            target_organs=["runtime"],
            proposed_change="bounded implementation change",
            expected_metrics={"quality": {"baseline": 1, "candidate": 2}},
            regression_plan="run regression suite",
            rollback_plan="restore previous accepted body",
        )

    first = create_revision("candidate one")
    first_claim = BodyRevisionStore.evaluation_claim(
        revision_id=first,
        tests_passed=True,
        organism_healthy=True,
        continuity_status="continuous",
        metrics={},
    )
    first_receipt = registry.issue(
        AUTHORITY,
        claim=first_claim,
        evidence="evaluation one",
        nonce="shared-nonce",
    )
    updated, report = store.evaluate(
        first,
        tests_passed=True,
        organism_healthy=True,
        continuity_status="continuous",
        metrics={},
        evidence="evaluation one",
        verification_receipt=first_receipt,
    )
    assert report.accepted is True
    assert updated.status == "validated"

    second = create_revision("candidate two")
    second_claim = BodyRevisionStore.evaluation_claim(
        revision_id=second,
        tests_passed=True,
        organism_healthy=True,
        continuity_status="continuous",
        metrics={},
    )
    second_receipt = registry.issue(
        AUTHORITY,
        claim=second_claim,
        evidence="evaluation two",
        nonce="shared-nonce",
    )
    with pytest.raises(PermissionError, match="already consumed"):
        store.evaluate(
            second,
            tests_passed=True,
            organism_healthy=True,
            continuity_status="continuous",
            metrics={},
            evidence="evaluation two",
            verification_receipt=second_receipt,
        )
    assert store.get(second).status == "proposed"


def test_verified_obligation_receipt_cannot_be_replayed(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    registry = _registry()
    store = MetabolismStore(database, verification_registry=registry)
    due = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    evidence = "provider invoice recurring-001"
    claim = MetabolismStore.obligation_claim(
        name="compute",
        asset="cash",
        unit="USD",
        amount=30,
        cadence_seconds=30 * 86_400,
        next_due_at=due.isoformat(),
        essential=True,
        source="trusted_adapter",
    )
    receipt = registry.issue(AUTHORITY, claim=claim, evidence=evidence, nonce="obligation-001")

    first = store.record_obligation(
        name="compute",
        asset="cash",
        unit="USD",
        amount=30,
        cadence_seconds=30 * 86_400,
        next_due_at=due,
        essential=True,
        source="trusted_adapter",
        evidence=evidence,
        verified=True,
        verification_receipt=receipt,
    )
    assert store.obligation(first).verified is True

    with pytest.raises(PermissionError, match="already consumed"):
        store.record_obligation(
            name="compute",
            asset="cash",
            unit="USD",
            amount=30,
            cadence_seconds=30 * 86_400,
            next_due_at=due,
            essential=True,
            source="trusted_adapter",
            evidence=evidence,
            verified=True,
            verification_receipt=receipt,
        )
    assert len(store.active(verified=True)) == 1
