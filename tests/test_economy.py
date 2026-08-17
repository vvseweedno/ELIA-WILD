from __future__ import annotations

from pathlib import Path

import pytest

from elia.brain import Decision
from elia.config import BrainConfig, Config, RuntimeConfig
from elia.economy import EconomyStore
from elia.runtime import EliaRuntime
from elia.tools import ToolRegistry


def make_config(tmp_path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    return Config(
        identity_name="ELIA",
        identity_statement="Economic continuity test seed.",
        mission=["preserve continuity", "seek legitimate useful work"],
        brain=BrainConfig(
            backend="mock",
            model_id="mock",
            base_url="http://127.0.0.1:8000/v1",
            timeout_seconds=5,
            max_tokens=128,
            temperature=0.7,
            top_p=0.8,
            thinking=False,
        ),
        runtime=RuntimeConfig(
            state_dir=tmp_path / ".elia",
            cycle_sleep_seconds=0,
            max_action_output_chars=16000,
            weekly_gpu_budget_hours=30,
            memory_recall_limit=12,
        ),
        raw_tools={"http_get": {"enabled": True}},
        subject_core_path=root / "config" / "subject_core.yaml",
        continuity_constitution_path=root / "config" / "continuity_constitution.yaml",
        system_prompt_path=root / "config" / "system_prompt.md",
        skills_dir=root / "skills",
    )


class OpportunityBrain:
    def decide(self, context):
        return Decision(
            objective="Record an evidence-backed work opportunity.",
            summary="The source describes a possible paid task; value remains an estimate.",
            action_name="noop",
            skill_name="resource_conservation",
            opportunity_updates=[
                {
                    "op": "create",
                    "title": "Synthetic paid task",
                    "kind": "work",
                    "source_url": "https://example.com/task",
                    "evidence": "Public page states a fixed reward for accepted work.",
                    "estimated_value": 1000,
                    "estimated_cost_value": 100,
                    "unit": "USD",
                    "probability": 0.25,
                    "estimated_gpu_hours": 0.5,
                }
            ],
            sleep_seconds=0,
        )


def test_unverified_resource_event_never_changes_verified_balance(tmp_path: Path) -> None:
    economy = EconomyStore(tmp_path / "memory.sqlite3")
    economy.record_resource_event(
        asset="cash",
        unit="USD",
        amount=500,
        kind="claimed_income",
        source="model_report",
        evidence="model said it happened",
        verified=False,
    )
    assert economy.verified_balance("cash", "USD") == 0
    summary = economy.resource_summary()[0]
    assert summary["verified_balance"] == 0
    assert summary["unverified_delta"] == 500


def test_verified_resource_event_requires_authority_and_evidence(tmp_path: Path) -> None:
    economy = EconomyStore(tmp_path / "memory.sqlite3")
    with pytest.raises(ValueError, match="verification_authority"):
        economy.record_resource_event(
            asset="cash",
            unit="USD",
            amount=100,
            kind="payment",
            source="trusted_adapter",
            verified=True,
        )

    economy.record_resource_event(
        asset="cash",
        unit="USD",
        amount=100,
        kind="payment",
        source="trusted_adapter",
        evidence="receipt:example-001",
        verified=True,
        verification_authority="payment_adapter:test",
    )
    assert economy.verified_balance("cash", "USD") == 100


def test_opportunity_scoring_uses_expected_net_value_per_gpu_hour(tmp_path: Path) -> None:
    economy = EconomyStore(tmp_path / "memory.sqlite3")
    low = economy.create_opportunity(
        title="Large but expensive",
        kind="work",
        evidence="test evidence",
        estimated_value=1000,
        estimated_cost_value=100,
        probability=0.5,
        estimated_gpu_hours=10,
        unit="USD",
    )
    high = economy.create_opportunity(
        title="Small efficient task",
        kind="work",
        evidence="test evidence",
        estimated_value=200,
        estimated_cost_value=20,
        probability=0.8,
        estimated_gpu_hours=0.5,
        unit="USD",
    )
    ranked = economy.active_opportunities()
    assert ranked[0].id == high
    assert ranked[1].id == low
    assert ranked[0].value_per_gpu_hour > ranked[1].value_per_gpu_hour


def test_terminal_opportunity_requires_evidence(tmp_path: Path) -> None:
    economy = EconomyStore(tmp_path / "memory.sqlite3")
    opportunity_id = economy.create_opportunity(
        title="Test opportunity",
        kind="work",
        evidence="source evidence",
        estimated_value=100,
        probability=0.5,
        unit="USD",
    )
    with pytest.raises(ValueError, match="requires evidence"):
        economy.update_opportunity(opportunity_id, status="won")
    won = economy.update_opportunity(
        opportunity_id,
        status="won",
        evidence="External acceptance receipt ABC-123",
        event="accepted",
    )
    assert won.status == "won"
    assert economy.verified_balance("cash", "USD") == 0


def test_model_opportunity_estimate_cannot_mint_verified_balance(tmp_path: Path) -> None:
    runtime = EliaRuntime(make_config(tmp_path), brain=OpportunityBrain())
    before = runtime.economy.verified_balance("cash", "USD")
    report = runtime.cycle()
    after = runtime.economy.verified_balance("cash", "USD")
    assert report["opportunity_changes"][0]["ok"] is True
    assert before == after == 0
    opportunities = runtime.economy.active_opportunities()
    assert len(opportunities) == 1
    assert opportunities[0].estimated_value == 1000


def test_stage_deliverable_never_submits_externally(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    tools = ToolRegistry(workspace)
    result = tools.execute(
        "stage_deliverable",
        {
            "title": "Candidate answer",
            "content": "Useful work product",
            "format": "markdown",
            "opportunity_id": 7,
            "validation": "Checked against public acceptance criteria.",
            "evidence": "source requirements captured",
        },
    )
    assert result.ok is True
    assert result.data["status"] == "staged_only"
    assert (workspace / result.data["path"]).is_file()
