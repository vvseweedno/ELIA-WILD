from __future__ import annotations

from pathlib import Path

from elia.brain import Decision
from elia.config import BrainConfig, Config, RuntimeConfig
from elia.organism_runtime import OrganismRuntime


def _config(tmp_path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    return Config(
        identity_name="ELIA",
        identity_statement="Organism runtime integration test seed.",
        mission=["learn from verified outcomes", "preserve continuity"],
        brain=BrainConfig(
            backend="mock",
            model_id="mock",
            base_url="http://127.0.0.1:8000/v1",
            timeout_seconds=5,
            max_tokens=128,
            temperature=0.0,
            top_p=1.0,
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


class ExperienceBrain:
    def __init__(self) -> None:
        self.contexts: list[dict] = []

    def decide(self, context: dict) -> Decision:
        self.contexts.append(context)
        if len(self.contexts) == 1:
            return Decision(
                objective="Create one revisable world hypothesis.",
                summary="Commit an observed hypothesis to the world model, not as verified fact.",
                action_name="world_model_propose",
                prediction={
                    "action_success_probability": 0.95,
                    "expected_outcome": "A hypothesis with capped model confidence is stored.",
                    "expected_information_gain": 0.1,
                    "expected_value": 0,
                    "unit": "VALUE_UNIT",
                },
                action_args={
                    "domain": "integration_test",
                    "subject": "prior-cycle",
                    "predicate": "changes",
                    "object": "future-context",
                    "confidence": 0.99,
                    "evidence": "This is an explicit integration-test proposition.",
                },
                sleep_seconds=0,
            )
        return Decision(
            objective="Observe whether lived state persisted into this wake.",
            summary="No further state mutation required.",
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


def test_first_cycle_experience_is_automatically_present_in_second_context(tmp_path: Path) -> None:
    brain = ExperienceBrain()
    runtime = OrganismRuntime(_config(tmp_path), brain=brain)
    first = runtime.cycle()
    second = runtime.cycle()

    assert first["result"]["ok"] is True
    assert second["result"]["ok"] is True
    assert len(brain.contexts) == 2

    next_context = brain.contexts[1]
    beliefs = next_context["world_model"]["beliefs"]
    assert any(
        item["subject"] == "prior-cycle"
        and item["predicate"] == "changes"
        and item["object"] == "future-context"
        for item in beliefs
    )
    assert any(
        item["action_name"] == "world_model_propose"
        for item in next_context["causal_memory"]["recent_interventions"]
    )
    assert any(
        item["source_ref"] == "world_model_propose"
        for item in next_context["sensorium"]
    )
    assert next_context["digital_body"]["capability_count"] >= 1
    assert next_context["organism_state_bus"]["incomplete_count"] >= 1
    # One cognitive-cycle transaction is open while the context is being assembled.

    assert runtime.tools.state_bus.incomplete() == []
    assert first["organism_transaction_id"] != second["organism_transaction_id"]
    for tx_id in (first["organism_transaction_id"], second["organism_transaction_id"]):
        valid, error = runtime.tools.state_bus.verify(tx_id)
        assert valid is True, error
