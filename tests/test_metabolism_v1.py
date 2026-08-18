from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from elia.economy import EconomyStore
from elia.memory import MemoryStore
from elia.metabolism import MetabolismEngine, MetabolismStore, SECONDS_PER_DAY
from elia.verification import VerificationRegistry


NOW = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)
VERIFY_KEY = b"metabolism-test-verifier-key-32bytes!"


def _registry() -> VerificationRegistry:
    return VerificationRegistry(
        {
            "test:ledger": VERIFY_KEY,
            "test:infrastructure": VERIFY_KEY,
            "test:billing": VERIFY_KEY,
        }
    )


def _verified_balance(economy: EconomyStore, *, asset: str, unit: str, amount: float) -> None:
    evidence = f"test receipt for {asset}/{unit}"
    claim = EconomyStore.resource_claim(
        asset=asset,
        unit=unit,
        amount=amount,
        kind="trusted_balance_adjustment",
        source="test_adapter",
    )
    registry = economy.verification_registry
    assert registry is not None
    receipt = registry.issue("test:ledger", claim=claim, evidence=evidence)
    economy.record_resource_event(
        asset=asset,
        unit=unit,
        amount=amount,
        kind="trusted_balance_adjustment",
        source="test_adapter",
        evidence=evidence,
        verified=True,
        verification_receipt=receipt,
    )


def _verified_obligation(
    store: MetabolismStore,
    *,
    name: str,
    asset: str,
    unit: str,
    amount: float,
    cadence_seconds: float = SECONDS_PER_DAY,
    due_days: float = 1.0,
    essential: bool = True,
) -> int:
    due = NOW + timedelta(days=due_days)
    due_text = due.isoformat()
    evidence = f"contract evidence for {name}"
    claim = MetabolismStore.obligation_claim(
        name=name,
        asset=asset,
        unit=unit,
        amount=amount,
        cadence_seconds=cadence_seconds,
        next_due_at=due_text,
        essential=essential,
        source="test_infrastructure",
    )
    registry = store.verification_registry
    assert registry is not None
    receipt = registry.issue("test:infrastructure", claim=claim, evidence=evidence)
    return store.record_obligation(
        name=name,
        asset=asset,
        unit=unit,
        amount=amount,
        cadence_seconds=cadence_seconds,
        next_due_at=due,
        essential=essential,
        source="test_infrastructure",
        evidence=evidence,
        verified=True,
        verification_receipt=receipt,
    )


def test_verified_obligation_rejects_plain_authority_string(tmp_path: Path) -> None:
    store = MetabolismStore(tmp_path / "memory.sqlite3")
    with pytest.raises(ValueError, match="signed VerificationReceipt"):
        store.record_obligation(
            name="Compute bill",
            asset="cash",
            unit="USD",
            amount=10,
            cadence_seconds=SECONDS_PER_DAY,
            next_due_at=NOW,
            essential=True,
            source="model_claim",
            evidence="claimed invoice",
            verified=True,
            verification_authority="model:says-trusted",
        )


def test_verified_obligation_receipt_binds_exact_claim_and_evidence(tmp_path: Path) -> None:
    registry = _registry()
    store = MetabolismStore(tmp_path / "memory.sqlite3", verification_registry=registry)
    evidence = "contract receipt A"
    claim = MetabolismStore.obligation_claim(
        name="Compute bill",
        asset="cash",
        unit="USD",
        amount=10,
        cadence_seconds=SECONDS_PER_DAY,
        next_due_at=NOW.isoformat(),
        essential=True,
        source="test_infrastructure",
    )
    receipt = registry.issue("test:infrastructure", claim=claim, evidence=evidence)
    obligation_id = store.record_obligation(
        name="Compute bill",
        asset="cash",
        unit="USD",
        amount=10,
        cadence_seconds=SECONDS_PER_DAY,
        next_due_at=NOW,
        essential=True,
        source="test_infrastructure",
        evidence=evidence,
        verified=True,
        verification_receipt=receipt,
    )
    assert store.obligation(obligation_id).verified is True

    with pytest.raises(PermissionError, match="claim digest mismatch"):
        store.record_obligation(
            name="Compute bill",
            asset="cash",
            unit="USD",
            amount=1000,
            cadence_seconds=SECONDS_PER_DAY,
            next_due_at=NOW,
            essential=True,
            source="test_infrastructure",
            evidence=evidence,
            verified=True,
            verification_receipt=receipt,
        )


def test_unverified_obligation_never_creates_runway_pressure(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    registry = _registry()
    economy = EconomyStore(db, verification_registry=registry)
    obligations = MetabolismStore(db, verification_registry=registry)
    _verified_balance(economy, asset="cash", unit="USD", amount=100)
    obligations.record_obligation(
        name="Unverified scary invoice",
        asset="cash",
        unit="USD",
        amount=1000,
        cadence_seconds=SECONDS_PER_DAY,
        next_due_at=NOW,
        essential=True,
        source="brain",
        evidence="A model suspects this invoice may exist.",
        verified=False,
    )

    snapshot = MetabolismEngine(db, weekly_gpu_budget_hours=30).snapshot(now=NOW)
    assert snapshot.resources == ()
    assert snapshot.bottleneck is None
    assert len(snapshot.unverified_obligations) == 1


def test_runway_is_vector_and_never_sums_unrelated_units(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    registry = _registry()
    economy = EconomyStore(db, verification_registry=registry)
    obligations = MetabolismStore(db, verification_registry=registry)
    _verified_balance(economy, asset="cash", unit="USD", amount=100)
    _verified_balance(economy, asset="cash", unit="RUB", amount=100_000)
    _verified_balance(economy, asset="api_credit", unit="CREDIT", amount=50)

    _verified_obligation(
        obligations,
        name="Hosting USD",
        asset="cash",
        unit="USD",
        amount=10,
    )
    _verified_obligation(
        obligations,
        name="API credits",
        asset="api_credit",
        unit="CREDIT",
        amount=5,
    )

    resources = MetabolismEngine(db, weekly_gpu_budget_hours=30).resource_runway(now=NOW)
    by_key = {(item.asset, item.unit): item for item in resources}
    assert set(by_key) == {("cash", "USD"), ("api_credit", "CREDIT")}
    assert by_key[("cash", "USD")].runway_days == pytest.approx(10.0)
    assert by_key[("api_credit", "CREDIT")].runway_days == pytest.approx(10.0)
    assert ("cash", "RUB") not in by_key


def test_negative_balance_has_zero_runway_and_uncovered_due(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    registry = _registry()
    economy = EconomyStore(db, verification_registry=registry)
    obligations = MetabolismStore(db, verification_registry=registry)
    _verified_balance(economy, asset="cash", unit="USD", amount=-5)
    _verified_obligation(
        obligations,
        name="Hosting",
        asset="cash",
        unit="USD",
        amount=10,
    )
    item = MetabolismEngine(db, weekly_gpu_budget_hours=30).resource_runway(now=NOW)[0]
    assert item.verified_balance == -5
    assert item.runway_days == 0
    assert item.next_due_covered is False


def test_compute_energy_uses_actual_runtime_metrics(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    memory = MemoryStore(db)
    memory.add_runtime_seconds(7.5 * 3600)
    memory.add_brain_seconds(2.25 * 3600)

    energy = MetabolismEngine(db, weekly_gpu_budget_hours=30).compute_energy(now=NOW)
    assert energy.weekly_limit == 30
    assert energy.used == pytest.approx(7.5)
    assert energy.remaining == pytest.approx(22.5)
    assert energy.remaining_ratio == pytest.approx(0.75)
    assert energy.brain_hours_used == pytest.approx(2.25)
    assert energy.seconds_until_reset > 0
    assert datetime.fromisoformat(energy.reset_at).weekday() == 0


def test_bottleneck_is_lowest_essential_verified_runway(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    registry = _registry()
    economy = EconomyStore(db, verification_registry=registry)
    obligations = MetabolismStore(db, verification_registry=registry)
    _verified_balance(economy, asset="cash", unit="USD", amount=100)
    _verified_balance(economy, asset="api", unit="CREDIT", amount=20)
    _verified_obligation(
        obligations,
        name="Hosting",
        asset="cash",
        unit="USD",
        amount=10,
    )
    _verified_obligation(
        obligations,
        name="Inference API",
        asset="api",
        unit="CREDIT",
        amount=5,
    )
    snapshot = MetabolismEngine(db, weekly_gpu_budget_hours=30).snapshot(now=NOW)
    assert snapshot.bottleneck is not None
    assert snapshot.bottleneck["asset"] == "api"
    assert snapshot.bottleneck["unit"] == "CREDIT"
    assert snapshot.bottleneck["runway_days"] == pytest.approx(4.0)


def test_due_advance_and_deactivation_require_signed_receipts(tmp_path: Path) -> None:
    registry = _registry()
    store = MetabolismStore(tmp_path / "memory.sqlite3", verification_registry=registry)
    obligation_id = _verified_obligation(
        store,
        name="Hosting",
        asset="cash",
        unit="USD",
        amount=10,
    )
    before = store.obligation(obligation_id)
    assert before is not None

    advance_evidence = "two verified billing periods processed"
    advance_receipt = registry.issue(
        "test:billing",
        claim=MetabolismStore.mutation_claim(
            obligation_id=obligation_id,
            event="due_advanced",
            periods=2,
        ),
        evidence=advance_evidence,
    )
    advanced = store.advance_due(
        obligation_id,
        periods=2,
        evidence=advance_evidence,
        verification_receipt=advance_receipt,
    )
    assert datetime.fromisoformat(advanced.next_due_at) > datetime.fromisoformat(before.next_due_at)

    with pytest.raises(ValueError, match="signed VerificationReceipt"):
        store.deactivate(
            obligation_id,
            evidence="claimed termination",
            authority="model:trusted",
        )

    deactivate_evidence = "verified contract terminated"
    deactivate_receipt = registry.issue(
        "test:billing",
        claim=MetabolismStore.mutation_claim(
            obligation_id=obligation_id,
            event="deactivated",
        ),
        evidence=deactivate_evidence,
    )
    inactive = store.deactivate(
        obligation_id,
        evidence=deactivate_evidence,
        verification_receipt=deactivate_receipt,
    )
    assert inactive.active is False
