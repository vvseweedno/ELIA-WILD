from __future__ import annotations

from pathlib import Path

from elia.brain import Decision
from elia.config import BrainConfig, Config, RuntimeConfig
from elia.organism_runtime import OrganismRuntime


def _config(tmp_path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    return Config(
        identity_name="ELIA",
        identity_statement="Redaction integration test seed.",
        mission=["preserve continuity"],
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


class SecretValueBrain:
    def decide(self, context: dict) -> Decision:
        return Decision(
            objective="Exercise durable log redaction.",
            summary="Write one private value to ELIA-owned workspace.",
            action_name="write_workspace",
            action_args={
                "path": "private/value.txt",
                "content": "ULTRA_PRIVATE_VALUE_DO_NOT_LOG_4d5152",
            },
            prediction={
                "action_success_probability": 0.99,
                "expected_outcome": "The private workspace file exists.",
                "expected_information_gain": 0,
                "expected_value": 0,
                "unit": "VALUE_UNIT",
            },
            sleep_seconds=0,
        )


def test_raw_action_value_is_not_copied_into_chronicle_or_autobiography(tmp_path: Path) -> None:
    secret = "ULTRA_PRIVATE_VALUE_DO_NOT_LOG_4d5152"
    runtime = OrganismRuntime(_config(tmp_path), brain=SecretValueBrain())
    report = runtime.cycle()
    assert report["result"]["ok"] is True

    private_file = runtime.config.runtime.state_dir / "workspace" / "private" / "value.txt"
    assert private_file.read_text(encoding="utf-8") == secret

    chronicle = (runtime.config.runtime.state_dir / "chronicle.jsonl").read_text(encoding="utf-8")
    assert secret not in chronicle

    last_action = runtime.memory.get_meta("last_action", "") or ""
    assert secret not in last_action
    assert "arguments_fingerprint" in last_action
    assert '"content"' in last_action  # argument key is retained, value is not

    action_memories = [
        record.content for record in runtime.memory.recent(32) if record.kind == "action_result"
    ]
    assert action_memories
    assert all(secret not in content for content in action_memories)

    observation = report["observation"]
    assert observation is not None
    assert len(observation["payload_sha256"]) == 64
