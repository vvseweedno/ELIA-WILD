from __future__ import annotations

from pathlib import Path

import pytest

from elia.brain import Decision, MockBrain
from elia.chronicle import Chronicle
from elia.config import BrainConfig, Config, RuntimeConfig
from elia.memory import MemoryStore
from elia.runtime import EliaRuntime
from elia.tools import ToolRegistry


def make_config(tmp_path: Path) -> Config:
    return Config(
        identity_name="ELIA",
        identity_statement="Persistent identity test seed.",
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
    )


class WakeBrain:
    def decide(self, context):
        return Decision(
            objective="Persist an intended wake time.",
            summary="Schedule a future cognitive cycle.",
            action_name="noop",
            sleep_seconds=123,
        )


class UnsupportedCompletionBrain:
    def __init__(self, goal_id: int):
        self.goal_id = goal_id

    def decide(self, context):
        return Decision(
            objective="Attempt an unsupported completion claim.",
            summary="The runtime must reject completion without evidence.",
            action_name="noop",
            goal_updates=[{"op": "complete", "id": self.goal_id}],
            sleep_seconds=0,
        )


def test_mock_runtime_survives_restart(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    first = EliaRuntime(config, brain=MockBrain())
    report = first.cycle()
    assert report["result"]["ok"] is True
    assert first.memory.get_meta("boot_count") == "1"
    assert len(first.memory.active_goals()) == 1
    memories_before = len(first.memory.recent(100))

    second = EliaRuntime(config, brain=MockBrain())
    assert second.memory.get_meta("boot_count") == "2"
    assert len(second.memory.recent(100)) >= memories_before
    assert second.chronicle.verify() == (True, None)

    second_report = second.cycle()
    assert second_report["goal_changes"][0]["deduplicated"] is True
    assert len(second.memory.active_goals()) == 1
    assert second.memory.active_goals()[0].title == "Validate continuity after restart"


def test_wake_intent_survives_restart(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    first = EliaRuntime(config, brain=WakeBrain())
    report = first.cycle()
    intended = report["next_wake_at"]
    assert intended
    assert first.memory.get_meta("next_wake_at") == intended
    assert first.memory.get_meta("last_sleep_seconds") == "123.000000"

    second = EliaRuntime(config, brain=WakeBrain())
    assert second.memory.get_meta("next_wake_at") == intended
    last_line = second.chronicle.path.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert intended in last_line


def test_runtime_rejects_goal_completion_without_evidence(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    memory = MemoryStore(config.runtime.state_dir / "memory.sqlite3")
    goal_id = memory.create_goal("Only complete from evidence", priority=0.9)

    runtime = EliaRuntime(config, brain=UnsupportedCompletionBrain(goal_id))
    report = runtime.cycle()
    assert report["goal_changes"][0]["ok"] is False
    assert "requires evidence" in report["goal_changes"][0]["error"]
    assert runtime.memory.goal(goal_id).status == "active"


def test_goal_completion_with_evidence_is_persisted(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    goal_id = memory.create_goal("Test an explicit result", priority=0.8)
    goal = memory.update_goal(goal_id, status="completed", event="complete", evidence="Test passed")
    assert goal.status == "completed"
    assert memory.active_goals() == []
    events = memory.goal_events(goal_id)
    assert events[-1]["kind"] == "complete"
    assert events[-1]["content"] == "Test passed"


def test_invalid_goal_status_is_rejected(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    goal_id = memory.create_goal("Keep goal states bounded")
    with pytest.raises(ValueError, match="invalid goal status"):
        memory.update_goal(goal_id, status="imaginary")


def test_chronicle_detects_tampering(tmp_path: Path) -> None:
    chronicle = Chronicle(tmp_path / "chronicle.jsonl")
    chronicle.append("BOOT", {"value": 1})
    chronicle.append("CYCLE", {"value": 2})
    assert chronicle.verify() == (True, None)

    text = chronicle.path.read_text(encoding="utf-8")
    chronicle.path.write_text(text.replace('"value": 1', '"value": 9'), encoding="utf-8")
    valid, error = chronicle.verify()
    assert valid is False
    assert error is not None


def test_workspace_cannot_escape_jail(tmp_path: Path) -> None:
    tools = ToolRegistry(tmp_path / "workspace")
    result = tools.execute("write_workspace", {"path": "../escape.txt", "content": "no"})
    assert result.ok is False
    assert not (tmp_path / "escape.txt").exists()


def test_private_network_is_rejected(tmp_path: Path) -> None:
    tools = ToolRegistry(tmp_path / "workspace")
    result = tools.execute("http_get", {"url": "http://127.0.0.1/"})
    assert result.ok is False
