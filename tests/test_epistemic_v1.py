from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from elia.brain import Decision
from elia.config import load_config
from elia.epistemic import (
    CognitiveBiographyStore,
    EpistemicAdjudication,
    EpistemicCortex,
    EpistemicPacket,
    EpistemicRegistry,
    parse_adjudication,
    parse_epistemic_packet,
)
from elia.epistemic_runtime import EpistemicOrganismRuntime
from elia.executive import ExecutivePolicy


ROOT = Path(__file__).resolve().parents[1]


def _registry() -> EpistemicRegistry:
    return EpistemicRegistry.load(ROOT / "config" / "epistemic.yaml")


def _config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("ELIA_STATE_DIR", str(tmp_path / ".elia"))
    monkeypatch.setenv("ELIA_BRAIN", "mock")
    return load_config(ROOT / "config" / "genesis.yaml")


class EpistemicFakeBrain:
    def __init__(self) -> None:
        self.organ_calls: list[str] = []
        self.judge_calls = 0
        self.decide_calls = 0
        self.last_context: dict | None = None

    def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        del max_tokens, temperature
        if "Epistemic Adjudicator" in system_prompt:
            self.judge_calls += 1
            ids = [int(value) for value in re.findall(r'"id"\s*:\s*(\d+)', user_prompt)]
            return json.dumps(
                {
                    "synthesis": "Prefer the claim with explicit falsifier while preserving dissent.",
                    "selected_packet_ids": ids[:2],
                    "confidence": 0.66,
                    "disagreements": ["One organ prefers more observation before action."],
                    "falsification_tests": ["Run one bounded observation that separates the claims."],
                    "recommended_focus": "Take the smallest discriminating observation.",
                }
            )
        match = re.search(r"cognitive organ inside ELIA WILD:\s*([^\n]+)", system_prompt)
        organ = match.group(1).strip() if match else "unknown"
        self.organ_calls.append(organ)
        return (
            f"CLAIM: {organ} proposes a distinct evidence-seeking view.\n"
            f"EVIDENCE: Verified context was inspected through the {organ} attention policy.\n"
            "COUNTEREXAMPLE: Another organ may identify a stronger contradictory observation.\n"
            "FALSIFIER: A direct observation contradicting this claim.\n"
            "UNCERTAINTY: External evidence remains incomplete.\n"
            "CONFIDENCE: 0.61"
        )

    def decide(self, context: dict) -> Decision:
        self.decide_calls += 1
        self.last_context = context
        return Decision(
            objective="Use the adjudicated epistemic state without treating it as authority.",
            summary="One Self makes one bounded action after differentiated deliberation.",
            action_name="noop",
            prediction={
                "action_success_probability": 0.99,
                "expected_outcome": "No external side effect.",
                "expected_information_gain": 0.0,
                "expected_value": 0.0,
                "unit": "VALUE_UNIT",
            },
            sleep_seconds=0,
        )


class ForbiddenBrain(EpistemicFakeBrain):
    def complete_text(self, **kwargs):  # type: ignore[override]
        raise AssertionError("no cognitive organ may run during Executive hibernation")

    def decide(self, context: dict) -> Decision:
        raise AssertionError("brain must not wake during Executive hibernation")


def test_registry_is_exact_pearson_12_with_evidence_and_dissent_roles() -> None:
    registry = _registry()
    assert len(registry.all()) == 12
    assert {item.id for item in registry.all()} == registry.EXPECTED_IDS
    assert any("evidence_anchor" in item.role_classes for item in registry.all())
    assert any("dissent" in item.role_classes for item in registry.all())


def test_plain_text_divergence_compiler_requires_claim_and_bounds_confidence() -> None:
    packet = parse_epistemic_packet(
        """CLAIM: Test a reversible hypothesis.\nEVIDENCE: observed A\nCOUNTEREXAMPLE: B\nFALSIFIER: C\nUNCERTAINTY: D\nCONFIDENCE: 4.2""",
        session_id="session",
        organ_id="sage",
    )
    assert packet.claim == "Test a reversible hypothesis."
    assert packet.confidence == 1.0
    with pytest.raises(ValueError, match="CLAIM"):
        parse_epistemic_packet("EVIDENCE: only evidence", session_id="s", organ_id="sage")


def test_adjudicator_cannot_select_packet_ids_that_do_not_exist() -> None:
    parsed = parse_adjudication(
        json.dumps(
            {
                "synthesis": "bounded",
                "selected_packet_ids": [2, 999, "bad"],
                "confidence": 0.7,
                "disagreements": [],
                "falsification_tests": [],
                "recommended_focus": "observe",
            }
        ),
        {1, 2, 3},
    )
    assert parsed.selected_packet_ids == (2,)


def test_biographies_are_isolated_and_outcome_is_not_labelled_truth(tmp_path: Path) -> None:
    store = CognitiveBiographyStore(tmp_path / "memory.sqlite3")
    session = store.begin_session(
        mode="mission",
        question="q",
        context_digest="a" * 64,
        selected_organs=["sage", "outlaw"],
    )
    sage = store.record_packet(
        EpistemicPacket(None, session, "sage", "claim-s", "e", "c", "f", "u", 0.8, "b" * 64)
    )
    store.record_packet(
        EpistemicPacket(None, session, "outlaw", "claim-o", "e", "c", "f", "u", 0.4, "c" * 64)
    )
    assert sage.id is not None
    store.finish_adjudication(
        session,
        EpistemicAdjudication("s", (int(sage.id),), 0.6, (), (), "observe"),
    )
    store.resolve_session(session, result_ok=True, action_name="noop", outcome_evidence="observed outcome")

    sage_bio = store.biography("sage")
    outlaw_bio = store.biography("outlaw")
    assert sage_bio["appearances"] == 1
    assert outlaw_bio["appearances"] == 1
    assert sage_bio["supported_count"] == 1
    assert outlaw_bio["supported_count"] == 0
    assert "not proof" in sage_bio["epistemic_warning"]
    assert sage_bio["recent"][0]["claim"] == "claim-s"
    assert outlaw_bio["recent"][0]["claim"] == "claim-o"


def test_selection_preserves_evidence_anchor_and_dissent(tmp_path: Path) -> None:
    registry = _registry()
    store = CognitiveBiographyStore(tmp_path / "memory.sqlite3")
    cortex = EpistemicCortex(registry, store)
    context = {
        "executive": {
            "mode": "mission",
            "focus": {"kind": "goal", "name": "architecture research", "reason": "test contradictions"},
            "cognitive_budget": {"tier": "deep", "wake_brain": True},
        },
        "needs": [],
        "active_goals": [],
    }
    selected = cortex.select_organs(context)
    assert len(selected) == registry.policy.deep_quorum
    assert any("evidence_anchor" in item.role_classes for item in selected)
    assert any("dissent" in item.role_classes for item in selected)


def test_executive_hibernation_never_invokes_epistemic_organs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(monkeypatch, tmp_path)
    config.runtime.weekly_gpu_budget_hours = 0.0
    brain = ForbiddenBrain()
    runtime = EpistemicOrganismRuntime(config, brain=brain)
    report = runtime.cycle()
    assert report["decision"]["action_name"] == "noop"
    assert brain.decide_calls == 0
    assert runtime.epistemic_store.recent_sessions(4) == []


def test_deep_cycle_runs_quorum_then_neutral_judge_then_single_self_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(monkeypatch, tmp_path)
    brain = EpistemicFakeBrain()
    policy = ExecutivePolicy(
        deep_focus_threshold=0.0,
        deep_budget_ratio=0.0,
        low_budget_ratio=0.0,
    )
    runtime = EpistemicOrganismRuntime(config, brain=brain, executive_policy=policy)
    runtime.memory.create_goal(
        "Evaluate a contested architecture decision",
        "Use differentiated evidence and preserve dissent.",
        priority=1.0,
        source="test",
    )
    report = runtime.cycle()

    assert report["epistemic"]["triggered"] is True
    assert len(brain.organ_calls) == runtime.epistemic_registry.policy.deep_quorum
    assert brain.judge_calls == 1
    assert brain.decide_calls == 1
    assert brain.last_context is not None
    assert brain.last_context["epistemic"]["adjudication"]["synthesis"]
    sessions = runtime.epistemic_store.recent_sessions(1)
    assert len(sessions) == 1
    assert sessions[0]["completed_at"] is not None
    assert sessions[0]["result_ok"] is True
