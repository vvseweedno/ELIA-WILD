from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from elia.brain import Decision
from elia.config import BrainConfig, Config, RuntimeConfig
from elia.economy import EconomyStore
from elia.metabolic_runtime import MetabolicOrganismRuntime
from elia.metabolism import MetabolismStore, SECONDS_PER_DAY
from elia.verification import VerificationRegistry


VERIFY_KEY = b"metabolic-runtime-verifier-key-32bytes"


def _registry() -> VerificationRegistry:
    return VerificationRegistry(
        {
            "test:ledger": VERIFY_KEY,
            "test:infrastructure": VERIFY_KEY,
        }
    )


def _config(tmp_path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    return Config(
        identity_name="ELIA",
        identity_statement="Metabolic runtime integration test seed.",
        mission=["preserve continuity", "use verified resource state"],
        brain=BrainConfig(
            backend="mock",
            model_id="mock",
            base_url="http://127.0.0.1:8000/v1",
            timeout_seconds=5,
            max_tokens=128,
            temperature=0,
            top_p=1,
            thinking=False,
        ),
        runtime=RuntimeConfig(
            state_dir=tmp_path / ".elia",
            cycle_sleep_seconds=0,
            max_action_output_chars=16000,
            weekly_gpu_budget_hours=30,
            memory_recall_limit=12,
        ),
        raw_tools={"http_get": {"enabled": False}, "body": {}},
        subject_core_path=root / "config" / "subject_core.yaml",
        continuity_constitution_path=root / "config" / "continuity_constitution.yaml",
        system_prompt_path=root / "config" / "system_prompt.md",
        skills_dir=root / "skills",
    )


class CaptureBrain:
    def __init__(self) -> None:
        self.contexts: list[dict] = []

    def decide(self, context: dict) -> Decision:
        self.contexts.append(context)
        return Decision(
            objective="Observe verified physiology.",
            summary="No external action is required for this integration test.",
            action_name="noop",
            prediction={
                "action_success_probability": 0.99,
                "expected_outcome": "No external side effect occurs.",
                "expected_information_gain": 0,
                "expected_value": 0,
                "unit": "VALUE_UNIT",
            },
            sleep_seconds=0,
        )


def _record_verified_balance(
    economy: EconomyStore,
    *,
    asset: str,
    unit: str,
    amount: float,
    kind: str,
    evidence: str,
) -> None:
    registry = economy.verification_registry
    assert registry is not None
    claim = EconomyStore.resource_claim(
        asset=asset,
        unit=unit,
        amount=amount,
        kind=kind,
        source="test_adapter",
    )
    receipt = registry.issue("test:ledger", claim=claim, evidence=evidence)
    economy.record_resource_event(
        asset=asset,
        unit=unit,
        amount=amount,
        kind=kind,
        source="test_adapter",
        evidence=evidence,
        verified=True,
        verification_receipt=receipt,
    )


def _seed_verified_resource(config: Config) -> None:
    db = config.runtime.state_dir / "memory.sqlite3"
    registry = _registry()
    economy = EconomyStore(db, verification_registry=registry)
    obligations = MetabolismStore(db, verification_registry=registry)
    _record_verified_balance(
        economy,
        asset="api",
        unit="CREDIT",
        amount=20,
        kind="verified_credit_balance",
        evidence="verified test credit receipt",
    )
    due = datetime.now(timezone.utc) + timedelta(days=1)
    evidence = "verified test inference obligation"
    claim = MetabolismStore.obligation_claim(
        name="Inference credits",
        asset="api",
        unit="CREDIT",
        amount=5,
        cadence_seconds=SECONDS_PER_DAY,
        next_due_at=due.isoformat(),
        essential=True,
        source="test_infrastructure",
    )
    receipt = registry.issue("test:infrastructure", claim=claim, evidence=evidence)
    obligations.record_obligation(
        name="Inference credits",
        asset="api",
        unit="CREDIT",
        amount=5,
        cadence_seconds=SECONDS_PER_DAY,
        next_due_at=due,
        essential=True,
        source="test_infrastructure",
        evidence=evidence,
        verified=True,
        verification_receipt=receipt,
    )


def test_verified_four_day_runway_becomes_deterministic_need(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed_verified_resource(config)
    brain = CaptureBrain()
    runtime = MetabolicOrganismRuntime(config, brain=brain)
    report = runtime.cycle()

    assert report["result"]["ok"] is True
    context = brain.contexts[0]
    metabolism = context["metabolism"]
    assert metabolism["bottleneck"]["asset"] == "api"
    assert metabolism["bottleneck"]["runway_days"] == 4.0
    assert any(item["name"] == "resource_runway" for item in context["needs"])
    # The balance covers the payments due on days 1..4, but the same verified
    # essential recurrence is uncovered on day 5. The cumulative projection therefore
    # raises an explicit near-term essential shortfall, not merely a 4-day runway hint.
    uncovered = next(
        item
        for item in context["needs"]
        if item["name"] == "uncovered_essential_obligation"
    )
    assert uncovered["severity"] == 0.92
    assert context["homeostasis"]["mode"] == "critical"


def test_unverified_obligation_does_not_create_resource_need(tmp_path: Path) -> None:
    config = _config(tmp_path)
    db = config.runtime.state_dir / "memory.sqlite3"
    registry = _registry()
    economy = EconomyStore(db, verification_registry=registry)
    obligations = MetabolismStore(db, verification_registry=registry)
    _record_verified_balance(
        economy,
        asset="cash",
        unit="USD",
        amount=100,
        kind="verified_cash",
        evidence="verified receipt",
    )
    obligations.record_obligation(
        name="Rumored bill",
        asset="cash",
        unit="USD",
        amount=1000,
        cadence_seconds=SECONDS_PER_DAY,
        next_due_at=datetime.now(timezone.utc),
        essential=True,
        source="brain",
        evidence="model suspects a bill",
        verified=False,
    )

    brain = CaptureBrain()
    runtime = MetabolicOrganismRuntime(config, brain=brain)
    runtime.cycle()
    context = brain.contexts[0]
    assert context["metabolism"]["resources"] == []
    assert context["metabolism"]["bottleneck"] is None
    assert not any(item["name"] == "resource_runway" for item in context["needs"])


def test_model_has_no_capability_to_create_verified_obligation_or_receipt(tmp_path: Path) -> None:
    runtime = MetabolicOrganismRuntime(_config(tmp_path), brain=CaptureBrain())
    catalog = runtime.tools.catalog()
    forbidden = {
        "record_resource_event",
        "verify_resource",
        "create_obligation",
        "verify_obligation",
        "issue_verification_receipt",
        "set_runway",
    }
    assert forbidden.isdisjoint(catalog)
