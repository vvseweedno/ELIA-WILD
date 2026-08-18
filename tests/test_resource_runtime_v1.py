from __future__ import annotations

import json
from pathlib import Path

from elia.brain import Decision
from elia.config import load_config
from elia.provider_context import provider_context
from elia.resource_runtime import ResourceOrganismRuntime


class EcologyBrain:
    def __init__(self) -> None:
        self.opportunity_id: int | None = None
        self.calls = 0

    def decide(self, context: dict) -> Decision:
        assert self.opportunity_id is not None
        self.calls += 1
        if self.calls == 1:
            return Decision(
                objective="Type the opportunity against the exact resource it may produce.",
                summary="Profile the public payment term without treating it as received money.",
                action_name="noop",
                opportunity_updates=[
                    {
                        "op": "profile_resource",
                        "opportunity_id": self.opportunity_id,
                        "target_asset": "cash",
                        "target_unit": "USD",
                        "target_amount": 120.0,
                        "eligibility_confidence": 0.8,
                        "evidence_quality": 0.75,
                        "evidence": "Public brief explicitly states 120 USD after accepted delivery.",
                        "blockers": ["submission channel still needs execution"],
                    }
                ],
                prediction={
                    "action_success_probability": 0.99,
                    "expected_outcome": "A local resource profile exists and verified balance remains unchanged.",
                    "expected_information_gain": 0.2,
                    "expected_value": 0,
                    "unit": "VALUE_UNIT",
                },
                sleep_seconds=0,
            )
        return Decision(
            objective="Create and stage one deliverable for the typed opportunity.",
            summary="Plan work first, then stage a local artifact only.",
            action_name="stage_deliverable",
            action_args={
                "title": "Opportunity audit response",
                "content": "A bounded candidate deliverable.",
                "format": "text",
                "opportunity_id": self.opportunity_id,
                "validation": "Contains the requested response and can be inspected locally.",
                "evidence": "Derived from the public brief.",
            },
            opportunity_updates=[
                {
                    "op": "plan_work",
                    "opportunity_id": self.opportunity_id,
                    "objective": "Produce the requested audit response.",
                    "deliverable_spec": "One concise text deliverable matching the public brief.",
                    "acceptance_criteria": "Artifact exists locally and matches the requested scope.",
                    "estimated_gpu_hours": 0.1,
                }
            ],
            prediction={
                "action_success_probability": 0.95,
                "expected_outcome": "A staged local deliverable is linked to the planned work item.",
                "expected_information_gain": 0.1,
                "expected_value": 0,
                "unit": "VALUE_UNIT",
            },
            sleep_seconds=0,
        )


def _runtime(monkeypatch, tmp_path: Path) -> tuple[ResourceOrganismRuntime, EcologyBrain]:
    monkeypatch.setenv("ELIA_STATE_DIR", str(tmp_path / ".elia"))
    config = load_config(Path(__file__).resolve().parents[1] / "config" / "genesis.yaml")
    brain = EcologyBrain()
    runtime = ResourceOrganismRuntime(config, brain=brain)
    opportunity_id = runtime.economy.create_opportunity(
        title="Public paid audit",
        kind="work",
        source_url="https://example.com/public-paid-audit",
        evidence="Public page offers 120 USD after accepted delivery.",
        estimated_value=120,
        estimated_cost_value=5,
        unit="VALUE_UNIT",
        probability=0.7,
        estimated_gpu_hours=0.5,
        source="test",
    )
    brain.opportunity_id = opportunity_id
    return runtime, brain


def test_resource_runtime_profiles_then_stages_without_claiming_submission(
    monkeypatch, tmp_path: Path
) -> None:
    runtime, brain = _runtime(monkeypatch, tmp_path)
    opportunity_id = int(brain.opportunity_id or 0)

    first = runtime.cycle()
    assert first["result"]["ok"] is True
    profile = runtime.resource_ecology_store.profile(opportunity_id)
    assert profile is not None
    assert profile.target_asset == "cash"
    assert profile.target_unit == "USD"
    assert runtime.economy.verified_balance("cash", "USD") == 0.0

    second = runtime.cycle()
    assert second["result"]["ok"] is True
    assert second["resource_ecology_transition"]["ok"] is True
    work = runtime.resource_ecology_store.work_for_opportunity(opportunity_id, 8)
    assert len(work) == 1
    assert work[0].status == "staged"
    assert work[0].artifact_path is not None
    assert work[0].submission_observation_id is None
    assert work[0].resource_event_id is None
    assert runtime.economy.verified_balance("cash", "USD") == 0.0

    payload = second["resource_ecology"]
    assert payload["active_work"][0]["status"] == "staged"


def test_staging_is_rejected_without_a_work_plan(monkeypatch, tmp_path: Path) -> None:
    runtime, brain = _runtime(monkeypatch, tmp_path)
    opportunity_id = int(brain.opportunity_id or 0)
    runtime.resource_ecology_store.upsert_profile(
        opportunity_id=opportunity_id,
        target_asset="cash",
        target_unit="USD",
        target_amount=120,
        eligibility_confidence=0.8,
        evidence_quality=0.8,
        evidence="Public payment term.",
    )
    result = runtime._execute_action(
        "stage_deliverable",
        {
            "title": "orphan",
            "content": "should not stage",
            "format": "text",
            "opportunity_id": opportunity_id,
            "validation": "none",
        },
    )
    assert result.ok is False
    assert "planned work item" in str(result.error)


def test_remote_provider_does_not_receive_raw_resource_ecology_evidence() -> None:
    context = {
        "resource_ecology": {
            "bottleneck": {"asset": "cash", "unit": "USD", "runway_days": 2},
            "exact_bottleneck_candidate_count": 1,
            "candidates": [
                {
                    "opportunity": {
                        "id": 9,
                        "title": "Paid task",
                        "source_url": "https://example.com/task",
                        "evidence": "PRIVATE_OPPORTUNITY_EVIDENCE",
                        "notes": "PRIVATE_NOTES",
                        "probability": 0.8,
                    },
                    "resource_profile": {
                        "opportunity_id": 9,
                        "target_asset": "cash",
                        "target_unit": "USD",
                        "target_amount": 50,
                        "evidence": "PRIVATE_PROFILE_EVIDENCE",
                        "eligibility_confidence": 0.7,
                        "evidence_quality": 0.8,
                        "blockers": ["needs eligibility check"],
                    },
                    "bottleneck_match": True,
                    "work_items": [
                        {
                            "id": 2,
                            "opportunity_id": 9,
                            "status": "submitted",
                            "objective": "do work",
                            "external_evidence": "PRIVATE_EXTERNAL_RESPONSE",
                        }
                    ],
                }
            ],
            "active_work": [],
            "unprofiled_opportunities": [],
            "epistemic_rule": "estimates are not receipts",
        }
    }
    public = provider_context(context)
    serialized = json.dumps(public, sort_keys=True)
    assert "PRIVATE_OPPORTUNITY_EVIDENCE" not in serialized
    assert "PRIVATE_PROFILE_EVIDENCE" not in serialized
    assert "PRIVATE_EXTERNAL_RESPONSE" not in serialized
    assert "PRIVATE_NOTES" not in serialized
    assert public["resource_ecology"]["candidates"][0]["resource_profile"]["target_unit"] == "USD"
