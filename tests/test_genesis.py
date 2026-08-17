from __future__ import annotations

from pathlib import Path

from elia.brain import MockBrain
from elia.chronicle import Chronicle
from elia.config import BrainConfig, Config, RuntimeConfig
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


def test_mock_runtime_survives_restart(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    first = EliaRuntime(config, brain=MockBrain())
    report = first.cycle()
    assert report["result"]["ok"] is True
    assert first.memory.get_meta("boot_count") == "1"
    memories_before = len(first.memory.recent(100))

    second = EliaRuntime(config, brain=MockBrain())
    assert second.memory.get_meta("boot_count") == "2"
    assert len(second.memory.recent(100)) >= memories_before
    assert second.chronicle.verify() == (True, None)


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
