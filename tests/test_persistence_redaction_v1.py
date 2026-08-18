from __future__ import annotations

from pathlib import Path
import sqlite3

from elia.brain import Decision
from elia.config import BrainConfig, Config, RuntimeConfig
from elia.runtime import EliaRuntime


SECRET = "BASE_RUNTIME_SECRET_MUST_STAY_PRIVATE_9f32c1"


def _config(tmp_path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    return Config(
        identity_name="ELIA",
        identity_statement="Persistence redaction test seed.",
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


class BaseSecretBrain:
    def decide(self, context: dict) -> Decision:
        return Decision(
            objective="Exercise base-runtime persistence boundaries.",
            summary="Write one private value to the private workspace.",
            action_name="write_workspace",
            action_args={"path": "private/base.txt", "content": SECRET},
            prediction={
                "action_success_probability": 0.99,
                "expected_outcome": "The private workspace write succeeds.",
                "expected_information_gain": 0,
                "expected_value": 0,
                "unit": "VALUE_UNIT",
            },
            sleep_seconds=0,
        )


def test_base_runtime_cannot_persist_raw_tool_values_outside_private_file(tmp_path: Path) -> None:
    runtime = EliaRuntime(_config(tmp_path), brain=BaseSecretBrain())
    report = runtime.cycle()
    assert report["result"]["ok"] is True

    private_file = runtime.config.runtime.state_dir / "workspace" / "private" / "base.txt"
    assert private_file.read_text(encoding="utf-8") == SECRET

    chronicle = runtime.chronicle.path.read_text(encoding="utf-8")
    assert SECRET not in chronicle
    assert "arguments_fingerprint" in chronicle

    last_action = runtime.memory.get_meta("last_action", "") or ""
    assert SECRET not in last_action
    assert "arguments_fingerprint" in last_action

    action_memories = [
        record.content for record in runtime.memory.recent(32) if record.kind == "action_result"
    ]
    assert action_memories
    assert all(SECRET not in content for content in action_memories)

    with sqlite3.connect(runtime.config.runtime.state_dir / "memory.sqlite3") as conn:
        rows = conn.execute(
            "SELECT observation_json FROM cognitive_forecasts WHERE resolved=1"
        ).fetchall()
    assert rows
    assert all(SECRET not in str(row[0]) for row in rows)
    assert all("data_fingerprint" in str(row[0]) for row in rows)

    # write_workspace returns path/byte-count metadata and deliberately does not echo
    # the model-controlled file content. The private file itself is the only durable
    # location that contains this input value.
    observations = runtime.tools.observations.snapshot(8)
    assert observations
    assert all(SECRET not in str(item.get("payload")) for item in observations)
    assert any(item.get("source_ref") == "write_workspace" for item in observations)
