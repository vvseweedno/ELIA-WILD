from __future__ import annotations

import json
from pathlib import Path

from elia.epistemic import CognitiveBiographyStore, EpistemicRegistry
from elia.epistemic_views import EvidenceViewProjector, EpistemicViewStore, ResilientEpistemicCortex


ROOT = Path(__file__).resolve().parents[1]


def _registry() -> EpistemicRegistry:
    return EpistemicRegistry.load(ROOT / "config" / "epistemic.yaml")


def _context() -> dict:
    return {
        "time_utc": "2026-08-18T12:00:00+00:00",
        "identity": {"name": "ELIA WILD"},
        "identity_contract": {"private_narrative": "must not drive evidence views"},
        "executive": {
            "mode": "mission",
            "focus": {"kind": "goal", "name": "architecture research", "reason": "contradiction exists"},
            "cognitive_budget": {"tier": "deep", "wake_brain": True},
        },
        "needs": [{"name": "epistemic_conflict", "severity": 0.8, "reason": "contradiction"}],
        "chronicle_integrity": {"valid": True},
        "active_goals": [{"id": 1, "title": "test architecture", "description": "compare views"}],
        "world_model": {
            "beliefs": [
                {"id": 1, "status": "verified", "subject": "A", "predicate": "is", "object": "observed"},
                {"id": 2, "status": "hypothesis", "subject": "B", "predicate": "may", "object": "exist"},
                {"id": 3, "status": "disputed", "subject": "C", "predicate": "causes", "object": "D"},
            ],
            "contradictions": [{"left": 2, "right": 3}],
            "epistemic_rule": "hypothesis is not verified fact",
        },
        "sensorium": [{"id": 10, "summary": "direct observation", "payload": "PRIVATE_RAW"}],
        "causal_memory": {"strategy_statistics": [{"action": "observe", "success_rate": 0.7}]},
        "metacognition": {"brier_mean": 0.2},
        "capabilities": {"catalog": {"noop": {"enabled": True}}},
        "skills": {"research": {"available": True}},
        "resource_ecology": {"bottleneck": None, "candidates": []},
        "recent_memory": [{"content": "prior experience"}],
        "self_hypotheses": [{"proposition": "adaptive self claim"}],
        "identity_drift": {"status": "stable"},
        "homeostasis": {"mode": "normal", "signals": []},
        "organism_state_bus": {"incomplete_count": 0},
        "last_action": {"name": "noop"},
        "work_ports": {"enabled": False, "ports": {}, "active_submissions": []},
        "resources": {"runtime_hours_remaining": 20},
        "metabolism": {"bottleneck": None},
        "digital_body": {"browser": {"enabled": False}},
        "lineage_head": {"branch_id": "main"},
        "executive_energy": {"overspend_ratio": 1.0},
        "chronological_recent_memory": [{"content": "recent event"}],
        "self_model": {"identity_id": "elia", "commitments": []},
    }


class FailingCouncilBrain:
    def __init__(self, *, fail_organs: int = 0, fail_judge: bool = False) -> None:
        self.fail_organs = fail_organs
        self.fail_judge = fail_judge
        self.organ_calls = 0

    def complete_text(self, *, system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
        del user_prompt, max_tokens, temperature
        if "Epistemic Adjudicator" in system_prompt:
            if self.fail_judge:
                raise RuntimeError("judge unavailable")
            return json.dumps(
                {
                    "synthesis": "bounded synthesis",
                    "selected_packet_ids": [1, 2],
                    "confidence": 0.6,
                    "disagreements": ["dissent remains"],
                    "falsification_tests": ["observe X"],
                    "recommended_focus": "observe X",
                }
            )
        self.organ_calls += 1
        if self.organ_calls <= self.fail_organs:
            raise RuntimeError("organ unavailable")
        return (
            "CLAIM: Gather a discriminating observation.\n"
            "EVIDENCE: Current evidence is incomplete.\n"
            "COUNTEREXAMPLE: Existing evidence may already decide the issue.\n"
            "FALSIFIER: Observe the disputed variable directly.\n"
            "UNCERTAINTY: The disputed causal relation.\n"
            "CONFIDENCE: 0.55"
        )


def test_evidence_views_are_structurally_different_and_privacy_bounded() -> None:
    projector = EvidenceViewProjector()
    context = _context()
    sage = projector.project("sage", context)
    hero = projector.project("hero", context)
    innocent = projector.project("innocent", context)
    outlaw = projector.project("outlaw", context)

    assert set(sage) != set(hero)
    assert "causal_memory" in sage and "causal_memory" not in hero
    assert "work_ports" in hero and "work_ports" not in sage
    assert "identity_contract" not in sage
    assert "PRIVATE_RAW" not in json.dumps(sage)
    assert [belief["status"] for belief in innocent["world_model"]["beliefs"]] == ["verified"]
    assert [belief["status"] for belief in outlaw["world_model"]["disputed_or_refuted"]] == ["disputed"]


def test_view_store_keeps_digest_and_fields_not_raw_context(tmp_path: Path) -> None:
    store = EpistemicViewStore(tmp_path / "memory.sqlite3")
    view = EvidenceViewProjector().project("sage", _context())
    digest = store.record("session", "sage", view)
    rows = store.session("session")
    assert len(rows) == 1
    assert rows[0]["view_digest"] == digest
    assert "world_model" in rows[0]["included_fields"]
    raw_db = (tmp_path / "memory.sqlite3").read_bytes()
    assert b"PRIVATE_RAW" not in raw_db


def test_one_failed_organ_preserves_packets_but_cannot_silently_lower_quorum(tmp_path: Path) -> None:
    registry = _registry()
    biography = CognitiveBiographyStore(tmp_path / "memory.sqlite3")
    views = EpistemicViewStore(tmp_path / "memory.sqlite3")
    cortex = ResilientEpistemicCortex(registry, biography, views)
    result = cortex.deliberate(FailingCouncilBrain(fail_organs=1), _context())
    assert result["triggered"] is True
    assert result["degraded"] is True
    assert len(result["successful_organs"]) >= 2
    assert len(result["failures"]) == 1
    assert result["required_quorum"] == registry.policy.deep_quorum
    assert result["achieved_quorum"] == registry.policy.deep_quorum - 1
    assert result["quorum_satisfied"] is False
    assert result["adjudication"]["selected_packet_ids"] == []
    assert result["adjudication"]["confidence"] == 0.0


def test_below_minimum_quorum_fails_closed_without_crashing(tmp_path: Path) -> None:
    registry = _registry()
    biography = CognitiveBiographyStore(tmp_path / "memory.sqlite3")
    views = EpistemicViewStore(tmp_path / "memory.sqlite3")
    cortex = ResilientEpistemicCortex(registry, biography, views)
    result = cortex.deliberate(FailingCouncilBrain(fail_organs=12), _context())
    assert result["triggered"] is True
    assert result["degraded"] is True
    assert result["successful_organs"] == []
    assert result["adjudication"]["selected_packet_ids"] == []
    assert result["adjudication"]["confidence"] == 0.0


def test_adjudicator_failure_preserves_packets_but_promotes_none(tmp_path: Path) -> None:
    registry = _registry()
    biography = CognitiveBiographyStore(tmp_path / "memory.sqlite3")
    views = EpistemicViewStore(tmp_path / "memory.sqlite3")
    cortex = ResilientEpistemicCortex(registry, biography, views)
    result = cortex.deliberate(FailingCouncilBrain(fail_judge=True), _context())
    assert result["degraded"] is True
    assert len(result["packets"]) >= 2
    assert result["adjudication"]["selected_packet_ids"] == []
    assert "no packet is promoted" in result["adjudication"]["synthesis"]
