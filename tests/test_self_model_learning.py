from __future__ import annotations

from pathlib import Path

import pytest

from elia.brain import Decision
from elia.config import BrainConfig, Config, RuntimeConfig
from elia.memory import MemoryStore
from elia.metacognition import MetacognitionStore
from elia.recall import RecallEngine
from elia.runtime import EliaRuntime
from elia.self_model import SelfHypothesisStore


def make_config(tmp_path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    return Config(
        identity_name="ELIA",
        identity_statement="Adaptive self-model test seed.",
        mission=["preserve continuity", "learn from verified outcomes"],
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


class ReflectionBrain:
    def decide(self, context):
        return Decision(
            objective="Update one revisable self hypothesis from verified local evidence.",
            summary="The workspace capability has repeatedly succeeded in this synthetic test context.",
            action_name="noop",
            skill_name="identity_reflection",
            prediction={
                "action_success_probability": 0.9,
                "expected_outcome": "No external side effect occurs.",
                "expected_information_gain": 0.1,
                "expected_value": 0,
                "unit": "VALUE_UNIT",
            },
            self_updates=[
                {
                    "op": "create",
                    "domain": "capability",
                    "proposition": "I can preserve evidence-backed adaptive claims outside one model call.",
                    "confidence": 0.99,
                    "evidence": "The runtime exposes a persistent self-hypothesis store and this update is being validated by regression tests.",
                }
            ],
            sleep_seconds=0,
        )


def test_self_hypothesis_requires_evidence_and_is_revisable(tmp_path: Path) -> None:
    store = SelfHypothesisStore(tmp_path / "memory.sqlite3")
    with pytest.raises(ValueError, match="requires evidence"):
        store.create(
            domain="capability",
            proposition="I can do anything",
            confidence=1.0,
            evidence="",
        )

    hypothesis_id = store.create(
        domain="limitation",
        proposition="A synthetic capability is unreliable.",
        confidence=0.7,
        evidence="Three deterministic failures in test fixture.",
    )
    updated = store.update(
        hypothesis_id,
        confidence=0.1,
        status="refuted",
        evidence="A corrected implementation passed repeated tests.",
        event="counterevidence",
    )
    assert updated.status == "refuted"
    assert store.active() == []


def test_recall_can_surface_old_important_goal_relevant_memory(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    important_id = memory.remember(
        "lesson",
        "Authenticated checkpoint rollback prevention is required for continuity.",
        importance=1.0,
        source="test",
    )
    for index in range(40):
        memory.remember(
            "action_result",
            f"routine irrelevant noise {index}",
            importance=0.1,
            source="test",
        )
    recalled = RecallEngine(memory).recall(
        queries=["continuity checkpoint rollback"],
        limit=6,
    )
    ids = {item["id"] for item in recalled}
    assert important_id in ids
    important = next(item for item in recalled if item["id"] == important_id)
    assert important["recall_components"]["lexical"] > 0


def test_metacognition_records_pre_action_probability_and_brier(tmp_path: Path) -> None:
    store = MetacognitionStore(tmp_path / "memory.sqlite3")
    forecast_id = store.record(
        objective="synthetic",
        action_name="noop",
        success_probability=0.8,
        expected_outcome="success",
    )
    brier = store.resolve(forecast_id, success=True, observation={"ok": True})
    assert brier == pytest.approx(0.04)
    calibration = store.calibration()
    assert calibration["resolved_forecasts"] == 1
    assert calibration["observed_success_rate"] == 1.0
    assert calibration["predicted_success_mean"] == 0.8
    assert calibration["execution_proxy_forecasts"] == 1
    assert calibration["outcome_calibration"]["resolved_outcomes"] == 0


def test_metacognition_calibrates_intended_outcome_not_tool_execution(tmp_path: Path) -> None:
    store = MetacognitionStore(tmp_path / "memory.sqlite3")
    forecast_id = store.record(
        objective="obtain the expected external result",
        action_name="synthetic_action",
        success_probability=0.8,
        expected_outcome="external result is accepted",
    )
    brier = store.resolve(
        forecast_id,
        success=True,
        outcome_success=False,
        observation={"ok": True, "data": {"accepted": False}},
    )
    calibration = store.calibration()

    assert brier == pytest.approx(0.64)
    assert calibration["execution_proxy_forecasts"] == 0
    assert calibration["outcome_calibration"]["resolved_outcomes"] == 1
    assert calibration["outcome_calibration"]["observed_success_rate"] == 0.0
    assert calibration["outcome_calibration"]["powered_claim_supported"] is False


def test_metacognition_rejects_out_of_range_probability_and_self_labeled_payload(
    tmp_path: Path,
) -> None:
    store = MetacognitionStore(tmp_path / "memory.sqlite3")
    with pytest.raises(ValueError, match=r"within \[0, 1\]"):
        store.record(
            objective="invalid",
            action_name="noop",
            success_probability=1.01,
        )

    forecast_id = store.record(
        objective="payload cannot grade itself",
        action_name="synthetic",
        success_probability=0.8,
    )
    brier = store.resolve(
        forecast_id,
        success=True,
        observation={"ok": True, "data": {"expected_outcome_met": False}},
    )
    calibration = store.calibration()

    assert brier == pytest.approx(0.04)
    assert calibration["execution_proxy_forecasts"] == 1
    assert calibration["outcome_calibration"]["resolved_outcomes"] == 0


def test_runtime_self_update_changes_adaptive_self_model_and_resolves_forecast(tmp_path: Path) -> None:
    runtime = EliaRuntime(make_config(tmp_path), brain=ReflectionBrain())
    before = runtime.memory.get_meta("self_model_fingerprint")
    report = runtime.cycle()
    after = runtime.memory.get_meta("self_model_fingerprint")

    assert report["assurance"]["accepted"] is True
    assert report["self_changes"][0]["ok"] is True
    assert report["self_changes"][0]["hypothesis_id"] == 1
    assert report["forecast"]["brier_score"] == pytest.approx(0.01)
    assert report["forecast"]["calibration"]["resolved_forecasts"] == 1
    assert before != after

    hypotheses = runtime.self_hypotheses.snapshot()
    assert len(hypotheses) == 1
    # Brain-supplied 0.99 confidence is capped until later evidence updates it.
    assert hypotheses[0]["confidence"] == 0.75
    latest = runtime.identity_store.latest_self_model()
    assert latest is not None
    assert latest["adaptive_hypotheses"][0]["proposition"] == hypotheses[0]["proposition"]
