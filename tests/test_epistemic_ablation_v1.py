from __future__ import annotations

from pathlib import Path
import re

from elia.epistemic import EpistemicRegistry
from elia.research.epistemic_ablation import EpistemicAblationHarness


ROOT = Path(__file__).resolve().parents[1]


class AblationBrain:
    def __init__(self) -> None:
        self.calls: list[tuple[int, float, str]] = []

    def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        del user_prompt
        self.calls.append((max_tokens, temperature, system_prompt))
        organ = "generic"
        match = re.search(r"temporary cognitive organ\s+([^\n(]+)", system_prompt, re.IGNORECASE)
        if match:
            organ = match.group(1).strip().lower().replace(" ", "_")
        elif "randomly assigned attention policy" in system_prompt:
            organ = "random_" + str(len(self.calls))
        elif "systems engineer" in system_prompt:
            organ = "systems"
        elif "adversarial reviewer" in system_prompt:
            organ = "adversarial"
        elif "research designer" in system_prompt:
            organ = "research"
        elif "representative-user" in system_prompt:
            organ = "user"
        elif "novelty reviewer" in system_prompt:
            organ = "novelty"
        elif "safety reviewer" in system_prompt:
            organ = "safety"
        elif "operations reviewer" in system_prompt:
            organ = "operations"
        elif "evidence reviewer" in system_prompt:
            organ = "evidence"
        return (
            f"CLAIM: {organ} claim about the test problem.\n"
            f"EVIDENCE: {organ} evidence policy inspected the same evidence.\n"
            f"COUNTEREXAMPLE: {organ} counterexample.\n"
            f"FALSIFIER: {organ} falsification test.\n"
            "UNCERTAINTY: shared uncertainty.\n"
            "CONFIDENCE: 0.6"
        )


def test_all_ablation_conditions_receive_identical_call_and_token_budget() -> None:
    registry = EpistemicRegistry.load(ROOT / "config" / "epistemic.yaml")
    harness = EpistemicAblationHarness(registry)
    brains: list[AblationBrain] = []

    def factory() -> AblationBrain:
        brain = AblationBrain()
        brains.append(brain)
        return brain

    result = harness.compare(
        factory,
        question="Which architecture is best supported?",
        public_context="same verified evidence for every condition",
        call_budget=5,
        max_tokens_per_call=180,
        seed=7,
    )
    assert result["equal_budget"] is True
    assert len(brains) == 4
    for brain in brains:
        assert len(brain.calls) == 5
        assert {call[0] for call in brain.calls} == {180}
    for condition in harness.CONDITIONS:
        metrics = result["conditions"][condition]["metrics"]
        assert metrics["call_count"] == 5
        assert metrics["max_tokens_per_call"] == 180
    assert "must not be inferred" in result["interpretation_boundary"]


def test_harness_measures_diversity_without_calling_it_accuracy() -> None:
    registry = EpistemicRegistry.load(ROOT / "config" / "epistemic.yaml")
    harness = EpistemicAblationHarness(registry)
    _, pearson = harness.run_condition(
        AblationBrain(),
        condition="pearson12",
        question="q",
        public_context="c",
        call_budget=5,
    )
    _, homogeneous = harness.run_condition(
        AblationBrain(),
        condition="homogeneous",
        question="q",
        public_context="c",
        call_budget=5,
    )
    assert pearson.unique_claim_ratio > homogeneous.unique_claim_ratio
    assert pearson.falsifier_coverage == 1.0
    assert homogeneous.falsifier_coverage == 1.0
    assert pearson.external_metrics == {}


def test_external_evaluator_is_the_only_path_to_accuracy_like_metrics() -> None:
    registry = EpistemicRegistry.load(ROOT / "config" / "epistemic.yaml")
    harness = EpistemicAblationHarness(registry)
    _, metrics = harness.run_condition(
        AblationBrain(),
        condition="pearson12",
        question="q",
        public_context="c",
        call_budget=3,
        external_evaluator=lambda responses: {
            "task_accuracy": sum(bool(item.claim) for item in responses) / len(responses)
        },
    )
    assert metrics.external_metrics == {"task_accuracy": 1.0}
