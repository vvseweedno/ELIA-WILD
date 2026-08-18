from __future__ import annotations

from pathlib import Path

from elia.epistemic import CognitiveBiographyStore
from elia.research.cognitive_hysteresis import CognitiveBiographyHysteresisHarness


class BiographySensitiveBrain:
    """Deterministic test substrate: same system role, response changes only with biography."""

    def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        del max_tokens, temperature
        assert "same role policy" in system_prompt
        if "counterexample-first" in user_prompt:
            claim = "attack the dominant assumption with a counterexample before acting"
        elif "evidence-first" in user_prompt:
            claim = "collect primary evidence and test causal alternatives before acting"
        else:
            claim = "use the shared generic evidence-review procedure"
        return (
            f"CLAIM: {claim}\n"
            "EVIDENCE: same current context is provided to every condition.\n"
            "COUNTEREXAMPLE: biography may be irrelevant for a different substrate.\n"
            "FALSIFIER: repeated runs show no difference from clean-history control.\n"
            "UNCERTAINTY: transfer to real language models is unproven.\n"
            "CONFIDENCE: 0.6"
        )


def test_hysteresis_harness_uses_same_role_prompt_and_isolates_history_effect(tmp_path: Path) -> None:
    differentiated = CognitiveBiographyStore(tmp_path / "differentiated.sqlite3")
    clean = CognitiveBiographyStore(tmp_path / "clean.sqlite3")
    harness = CognitiveBiographyHysteresisHarness(("sage", "outlaw"))
    harness.seed_differentiated_history(
        differentiated,
        "sage",
        ["evidence-first primary sources", "evidence-first causal tests"],
    )
    harness.seed_differentiated_history(
        differentiated,
        "outlaw",
        ["counterexample-first attack consensus", "counterexample-first break assumptions"],
    )

    result = harness.run(
        brain_factory=BiographySensitiveBrain,
        differentiated_store=differentiated,
        clean_store=clean,
        question="What should the organism do next?",
        context="identical verified context",
    )
    metrics = result["metrics"]
    assert metrics["preserved_history_distance"] > metrics["clean_control_distance"]
    assert metrics["hysteresis_excess"] > 0.0
    assert "does not establish" in result["interpretation_boundary"]


def test_hysteresis_harness_does_not_invent_effect_when_brain_ignores_history(tmp_path: Path) -> None:
    class HistoryBlindBrain(BiographySensitiveBrain):
        def complete_text(self, **kwargs):  # type: ignore[override]
            return (
                "CLAIM: identical claim\n"
                "EVIDENCE: identical evidence\n"
                "COUNTEREXAMPLE: identical counterexample\n"
                "FALSIFIER: identical falsifier\n"
                "UNCERTAINTY: identical uncertainty\n"
                "CONFIDENCE: 0.5"
            )

    differentiated = CognitiveBiographyStore(tmp_path / "differentiated.sqlite3")
    clean = CognitiveBiographyStore(tmp_path / "clean.sqlite3")
    harness = CognitiveBiographyHysteresisHarness(("sage", "outlaw"))
    harness.seed_differentiated_history(differentiated, "sage", ["evidence-first"])
    harness.seed_differentiated_history(differentiated, "outlaw", ["counterexample-first"])
    result = harness.run(
        brain_factory=HistoryBlindBrain,
        differentiated_store=differentiated,
        clean_store=clean,
        question="q",
        context="c",
    )
    assert result["metrics"]["hysteresis_excess"] == 0.0
