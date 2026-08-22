from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import elia.runtime as runtime_module
from elia.__main__ import _maybe_auto_checkpoint
from elia.brain import Decision
from elia.chronicle import Chronicle
from elia.config import BrainConfig, Config, RuntimeConfig
from elia.lifecycle import evaluate_preflight
from elia.memory import MemoryStore
from elia.owner_control import OwnerControl, OwnerMandate
from elia.runtime import EliaRuntime
from elia.transition_kernel import AcceptedTransitionGuard


class SleepBrain:
    def __init__(self, sleep_seconds: float = 60):
        self.sleep_seconds = sleep_seconds

    def decide(self, context):
        return Decision(
            objective="Conserve compute after one bounded action.",
            summary="Request a sleep long enough to leave the GPU session.",
            action_name="noop",
            sleep_seconds=self.sleep_seconds,
        )


def make_config(tmp_path: Path, *, auto_checkpoint: bool = False) -> Config:
    return Config(
        identity_name="ELIA",
        identity_statement="Lifecycle test seed.",
        mission=["preserve continuity", "conserve scarce compute"],
        brain=BrainConfig(
            backend="mock",
            model_id="fake-expensive-model",
            base_url="http://127.0.0.1:8000/v1",
            timeout_seconds=5,
            max_tokens=128,
            temperature=0.7,
            top_p=0.8,
            thinking=False,
        ),
        runtime=RuntimeConfig(
            state_dir=tmp_path / ".elia",
            cycle_sleep_seconds=60,
            max_action_output_chars=16000,
            weekly_gpu_budget_hours=30,
            memory_recall_limit=12,
            max_in_session_sleep_seconds=5,
            auto_checkpoint_path=(tmp_path / "auto.eliacp" if auto_checkpoint else None),
        ),
        raw_tools={"http_get": {"enabled": True}},
    )


def test_runtime_construction_does_not_load_brain(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_build(config):
        calls.append(config.model_id)
        return SleepBrain(0)

    monkeypatch.setattr(runtime_module, "build_brain", fake_build)
    runtime = EliaRuntime(make_config(tmp_path))
    assert runtime.brain_loaded is False
    assert calls == []

    runtime.cycle()
    assert runtime.brain_loaded is True
    assert calls == ["fake-expensive-model"]


def test_future_wake_hibernates_without_model(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    memory = MemoryStore(config.runtime.state_dir / "memory.sqlite3")
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    memory.set_meta("next_wake_at", future.isoformat())

    decision = evaluate_preflight(config.runtime.state_dir, 30)
    assert decision.mode == "hibernate"
    assert decision.seconds_until_wake is not None and decision.seconds_until_wake > 0


def test_force_wake_bypasses_schedule_only(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    memory = MemoryStore(config.runtime.state_dir / "memory.sqlite3")
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    memory.set_meta("next_wake_at", future.isoformat())

    decision = evaluate_preflight(config.runtime.state_dir, 30, force_wake=True)
    assert decision.mode == "wake"
    assert "Schedule guard bypassed" in decision.reason

    memory.add_runtime_seconds(30 * 3600)
    exhausted = evaluate_preflight(config.runtime.state_dir, 30, force_wake=True)
    assert exhausted.mode == "hibernate"
    assert "budget" in exhausted.reason.lower()


def test_chronicle_failure_halts_even_with_force_wake(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    chronicle = Chronicle(config.runtime.state_dir / "chronicle.jsonl")
    chronicle.append("BOOT", {"value": 1})
    chronicle.append("CYCLE", {"value": 2})
    text = chronicle.path.read_text(encoding="utf-8")
    chronicle.path.write_text(text.replace('"value": 1', '"value": 99'), encoding="utf-8")

    decision = evaluate_preflight(config.runtime.state_dir, 30, force_wake=True)
    assert decision.mode == "halt"
    assert "integrity failure" in decision.reason.lower()


def test_preflight_recovers_interrupted_transition_before_reading_baseline(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    memory = MemoryStore(config.runtime.state_dir / "memory.sqlite3")
    memory.set_meta("continuity_probe", "accepted")
    chronicle = Chronicle(config.runtime.state_dir / "chronicle.jsonl")
    chronicle.append("BOOT", {"accepted": True})

    guard = AcceptedTransitionGuard(config.runtime.state_dir, chronicle)
    guard.__enter__()
    memory.set_meta("continuity_probe", "dirty-after-crash")
    chronicle.append("CYCLE", {"accepted": False})
    guard._release()

    evaluate_preflight(config.runtime.state_dir, 30, force_wake=True)
    recovered = MemoryStore(config.runtime.state_dir / "memory.sqlite3")
    assert recovered.get_meta("continuity_probe") == "accepted"
    assert not (
        config.runtime.state_dir / "transition-kernel" / "active.json"
    ).exists()


def test_owner_kill_halts_preflight_even_when_force_wake_is_requested(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    database = config.runtime.state_dir / "memory.sqlite3"
    mandate = OwnerMandate(
        schema_version=1,
        precedence=("owner", "continuity"),
        require_external_lease=False,
        approval_required_actions=(),
        default_lease_hours=1.0,
        fingerprint="f" * 64,
    )
    OwnerControl(database, mandate).kill(reason="operator emergency stop")

    decision = evaluate_preflight(
        config.runtime.state_dir,
        30,
        force_wake=True,
    )
    assert decision.mode == "halt"
    assert "owner kill" in decision.reason.lower()


def test_long_sleep_becomes_hibernate_transition(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    runtime = EliaRuntime(config, brain=SleepBrain(60))
    outcome = runtime.run()
    assert outcome["state"] == "hibernating"
    assert runtime.memory.get_meta("lifecycle_state") == "hibernating"
    assert runtime.memory.get_meta("next_wake_at") is not None
    assert runtime.chronicle.verify() == (True, None)
    assert '"kind": "HIBERNATE"' in runtime.chronicle.path.read_text(encoding="utf-8")


def test_short_sleep_can_remain_inside_session(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.runtime.max_in_session_sleep_seconds = 5
    runtime = EliaRuntime(config, brain=SleepBrain(0))
    outcome = runtime.run(cycles=2)
    assert outcome["state"] == "paused"
    assert outcome["cycles_completed"] == 2


def test_clean_pause_can_auto_checkpoint(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path, auto_checkpoint=True)
    runtime = EliaRuntime(config, brain=SleepBrain(0))
    outcome = runtime.run(cycles=1)
    monkeypatch.setenv("ELIA_CHECKPOINT_KEY", "lifecycle-test-checkpoint-key-32bytes")

    checkpoint = _maybe_auto_checkpoint(config, "ELIA_CHECKPOINT_KEY", outcome)
    assert checkpoint is not None
    assert checkpoint["ok"] is True
    assert config.runtime.auto_checkpoint_path.is_file()
    assert len(checkpoint["digest"]) == 64
