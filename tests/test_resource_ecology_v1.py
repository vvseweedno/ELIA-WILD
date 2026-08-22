from __future__ import annotations

from pathlib import Path

import pytest

from elia.economy import EconomyStore
from elia.observations import ObservationStore
from elia.resource_ecology import ResourceEcologyEngine, ResourceEcologyStore
from elia.verification import VerificationRegistry


def _opportunity(economy: EconomyStore, *, title: str = "Paid audit") -> int:
    return economy.create_opportunity(
        title=title,
        kind="work",
        source_url="https://example.com/opportunity",
        evidence="Public brief says payment is available after accepted delivery.",
        estimated_value=100.0,
        estimated_cost_value=5.0,
        unit="VALUE_UNIT",
        probability=0.5,
        estimated_gpu_hours=1.0,
        source="test",
    )


def test_exact_resource_target_is_separate_from_abstract_value(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    economy = EconomyStore(db)
    ecology = ResourceEcologyStore(db)
    opportunity_id = _opportunity(economy)

    profile = ecology.upsert_profile(
        opportunity_id=opportunity_id,
        target_asset="cash",
        target_unit="USD",
        target_amount=80.0,
        eligibility_confidence=0.8,
        evidence_quality=0.75,
        evidence="Brief explicitly states an 80 USD fixed payment.",
        blockers=["account eligibility must still be checked"],
        source="brain",
    )

    assert profile.target_asset == "cash"
    assert profile.target_unit == "USD"
    assert profile.target_amount == 80.0
    assert profile.qualification_score == pytest.approx(0.6)
    assert profile.as_dict()["epistemic_status"] == "estimated_not_verified"
    assert economy.verified_balance("cash", "USD") == 0.0


def test_only_exact_asset_and_unit_match_counts_as_bottleneck_relief(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    economy = EconomyStore(db)
    ecology = ResourceEcologyStore(db)

    usd_id = _opportunity(economy, title="USD task")
    rub_id = _opportunity(economy, title="RUB task")
    ecology.upsert_profile(
        opportunity_id=usd_id,
        target_asset="cash",
        target_unit="USD",
        target_amount=40,
        eligibility_confidence=0.8,
        evidence_quality=0.8,
        evidence="USD payment stated.",
    )
    ecology.upsert_profile(
        opportunity_id=rub_id,
        target_asset="cash",
        target_unit="RUB",
        target_amount=100_000,
        eligibility_confidence=1.0,
        evidence_quality=1.0,
        evidence="RUB payment stated.",
    )

    snapshot = ResourceEcologyEngine(db).snapshot(
        {
            "bottleneck": {
                "asset": "cash",
                "unit": "USD",
                "runway_days": 2.0,
                "verified_daily_burn": 10.0,
            }
        }
    )
    candidates = snapshot["candidates"]
    assert candidates[0]["opportunity"]["id"] == usd_id
    assert candidates[0]["bottleneck_match"] is True
    assert candidates[0]["effective_success_probability"] == pytest.approx(0.4)
    assert candidates[0]["expected_runway_gain_days"] == pytest.approx(1.6)
    assert candidates[0]["expected_net_value"] == pytest.approx(35.0)
    rub = next(item for item in candidates if item["opportunity"]["id"] == rub_id)
    assert rub["bottleneck_match"] is False
    assert rub["expected_runway_gain_days"] is None


def test_zero_gpu_estimate_has_no_invented_per_gpu_denominator(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    economy = EconomyStore(db)
    ecology = ResourceEcologyStore(db)
    opportunity_id = economy.create_opportunity(
        title="No-compute credit",
        kind="grant",
        source_url="https://example.com/grant",
        evidence="Public grant terms.",
        estimated_value=10.0,
        estimated_cost_value=0.0,
        unit="VALUE_UNIT",
        probability=0.5,
        estimated_gpu_hours=0.0,
        source="test",
    )
    ecology.upsert_profile(
        opportunity_id=opportunity_id,
        target_asset="api",
        target_unit="CREDIT",
        target_amount=20.0,
        eligibility_confidence=0.5,
        evidence_quality=0.5,
        evidence="Eligibility remains estimated.",
    )

    candidate = ResourceEcologyEngine(db).candidates({})[0].as_dict()

    assert candidate["expected_resource_amount"] == pytest.approx(5.0)
    assert candidate["expected_resource_per_gpu_hour"] is None
    assert candidate["expected_net_value_per_gpu_hour"] is None


def test_resource_profile_rejects_probability_outside_unit_interval(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state.sqlite3"
    economy = EconomyStore(db)
    ecology = ResourceEcologyStore(db)
    opportunity_id = _opportunity(economy)

    with pytest.raises(ValueError, match=r"within \[0, 1\]"):
        ecology.upsert_profile(
            opportunity_id=opportunity_id,
            target_asset="cash",
            target_unit="USD",
            target_amount=100.0,
            eligibility_confidence=1.1,
            evidence_quality=0.5,
            evidence="Out-of-range fixture.",
        )


def test_work_requires_profile_and_staged_delivery_is_not_submission(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    economy = EconomyStore(db)
    ecology = ResourceEcologyStore(db)
    opportunity_id = _opportunity(economy)

    with pytest.raises(ValueError, match="resource profile"):
        ecology.create_work_item(
            opportunity_id=opportunity_id,
            objective="Deliver audit",
            deliverable_spec="Markdown audit",
            acceptance_criteria="All requested sections present",
        )

    ecology.upsert_profile(
        opportunity_id=opportunity_id,
        target_asset="cash",
        target_unit="USD",
        target_amount=100,
        eligibility_confidence=0.7,
        evidence_quality=0.8,
        evidence="Payment term found in brief.",
    )
    work = ecology.create_work_item(
        opportunity_id=opportunity_id,
        objective="Deliver audit",
        deliverable_spec="A reproducible Markdown audit with findings and evidence",
        acceptance_criteria="Requested scope covered and validation steps included",
        estimated_gpu_hours=0.5,
    )
    assert work.status == "planned"

    staged = ecology.attach_staged_deliverable(
        opportunity_id=opportunity_id,
        artifact_path="deliverables/audit.json",
        evidence="Local artifact written and validated.",
    )
    assert staged.status == "staged"
    assert staged.submission_observation_id is None
    assert economy.verified_balance("cash", "USD") == 0.0


def test_submission_requires_successful_observation(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    economy = EconomyStore(db)
    ecology = ResourceEcologyStore(db)
    observations = ObservationStore(db)
    opportunity_id = _opportunity(economy)
    ecology.upsert_profile(
        opportunity_id=opportunity_id,
        target_asset="cash",
        target_unit="USD",
        target_amount=100,
        eligibility_confidence=0.8,
        evidence_quality=0.8,
        evidence="Payment term found.",
    )
    work = ecology.create_work_item(
        opportunity_id=opportunity_id,
        objective="Deliver audit",
        deliverable_spec="Markdown audit",
        acceptance_criteria="Accepted by client",
    )
    ecology.attach_staged_deliverable(
        opportunity_id=opportunity_id,
        artifact_path="deliverables/audit.json",
    )
    failed = observations.record(
        source_kind="body",
        source_ref="browser_click",
        payload={"submitted": False},
        success=False,
        summary="submission failed",
    )
    with pytest.raises(ValueError, match="successful recorded observation"):
        ecology.record_submission(
            work_item_id=work.id,
            observation_id=failed.id,
            evidence="Attempted submission.",
        )

    success = observations.record(
        source_kind="body",
        source_ref="browser_click",
        payload={"submitted": True},
        success=True,
        summary="submission completed",
    )
    submitted = ecology.record_submission(
        work_item_id=work.id,
        observation_id=success.id,
        evidence="Observed successful submission response.",
    )
    assert submitted.status == "submitted"
    assert submitted.submission_observation_id == success.id


def test_realized_work_requires_matching_verified_positive_resource_event(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    registry = VerificationRegistry({"payment_adapter": b"0123456789abcdef0123456789abcdef"})
    economy = EconomyStore(db, verification_registry=registry)
    ecology = ResourceEcologyStore(db)
    observations = ObservationStore(db)
    opportunity_id = _opportunity(economy)
    ecology.upsert_profile(
        opportunity_id=opportunity_id,
        target_asset="cash",
        target_unit="USD",
        target_amount=100,
        eligibility_confidence=0.9,
        evidence_quality=0.9,
        evidence="100 USD payment term found.",
    )
    work = ecology.create_work_item(
        opportunity_id=opportunity_id,
        objective="Deliver audit",
        deliverable_spec="Markdown audit",
        acceptance_criteria="External acceptance recorded",
    )
    ecology.attach_staged_deliverable(
        opportunity_id=opportunity_id,
        artifact_path="deliverables/audit.json",
    )
    observation = observations.record(
        source_kind="body",
        source_ref="browser_click",
        payload={"submitted": True},
        success=True,
        summary="submitted",
    )
    ecology.record_submission(
        work_item_id=work.id,
        observation_id=observation.id,
        evidence="Submission confirmation observed.",
    )
    ecology.record_external_outcome(
        work_item_id=work.id,
        accepted=True,
        evidence="External acceptance confirmation observed.",
    )

    unverified_event = EconomyStore(db).record_resource_event(
        asset="cash",
        unit="USD",
        amount=100,
        kind="income",
        source="test",
        evidence="unverified claim",
        verified=False,
    )
    with pytest.raises(ValueError, match="not verified"):
        ecology.link_verified_resource_event(
            work_item_id=work.id,
            resource_event_id=unverified_event,
        )

    claim = EconomyStore.resource_claim(
        asset="cash",
        unit="RUB",
        amount=100.0,
        kind="income",
        source="payment_adapter",
    )
    receipt = registry.issue(
        "payment_adapter", claim=claim, evidence="provider receipt RUB"
    )
    wrong_unit = economy.record_resource_event(
        asset="cash",
        unit="RUB",
        amount=100,
        kind="income",
        source="payment_adapter",
        evidence="provider receipt RUB",
        verified=True,
        verification_receipt=receipt,
    )
    with pytest.raises(ValueError, match="does not match"):
        ecology.link_verified_resource_event(
            work_item_id=work.id,
            resource_event_id=wrong_unit,
        )

    claim = EconomyStore.resource_claim(
        asset="cash",
        unit="USD",
        amount=100.0,
        kind="income",
        source="payment_adapter",
    )
    receipt = registry.issue(
        "payment_adapter", claim=claim, evidence="provider receipt USD"
    )
    verified = economy.record_resource_event(
        asset="cash",
        unit="USD",
        amount=100,
        kind="income",
        source="payment_adapter",
        evidence="provider receipt USD",
        verified=True,
        verification_receipt=receipt,
    )
    realized = ecology.link_verified_resource_event(
        work_item_id=work.id,
        resource_event_id=verified,
        evidence="Payment matched accepted work.",
    )
    assert realized.status == "realized"
    assert realized.resource_event_id == verified
    assert economy.verified_balance("cash", "USD") == pytest.approx(100.0)
