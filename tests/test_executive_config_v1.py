from __future__ import annotations

from pathlib import Path

from elia.brain import Decision
from elia.config import BrainConfig, Config, ExecutiveConfig, RuntimeConfig, load_config
from elia.executive_runtime import ExecutiveOrganismRuntime


class CountingBrain:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, context: dict) -> Decision:
        self.calls += 1
        return Decision(
            objective="Exercise feature-level Executive rollback.",
            summary="Executive is disabled; use the Genesis 1.2 cognitive path.",
            action_name="noop",
            prediction={
                "action_success_probability": 0.99,
                "expected_outcome": "No external side effect.",
                "expected_information_gain": 0,
                "expected_value": 0,
                "unit": "VALUE_UNIT",
            },
            sleep_seconds=0,
        )


def _disabled_config(tmp_path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    return Config(
        identity_name="ELIA",
        identity_statement="Executive configuration rollback test seed.",
        mission=["preserve continuity"],
        brain=BrainConfig(
            backend="mock",
            model_id="mock",
            base_url="http://127.0.0.1:8000/v1",
            timeout_seconds=5,
            max_tokens=512,
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
        executive=ExecutiveConfig(enabled=False),
    )


def test_project_genesis_config_exposes_bounded_executive_policy() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "genesis.yaml")
    assert config.executive.enabled is True
    assert 0 < config.executive.low_tokens <= config.executive.normal_tokens <= config.executive.deep_tokens
    assert config.executive.maintenance_need_threshold <= config.executive.critical_need_threshold
    assert config.executive.low_budget_ratio <= config.executive.deep_budget_ratio
    assert config.executive.adaptive_thinking is True


def test_disabling_executive_is_feature_rollback_not_identity_fork(tmp_path: Path) -> None:
    config = _disabled_config(tmp_path)
    brain = CountingBrain()
    runtime = ExecutiveOrganismRuntime(config, brain=brain)
    report = runtime.cycle()
    assert brain.calls == 1
    assert report["executive"]["enabled"] is False
    assert runtime.executive_store.recent(8) == []
    assert runtime.identity.fingerprint == runtime.identity_store.last_lineage().identity_fingerprint
