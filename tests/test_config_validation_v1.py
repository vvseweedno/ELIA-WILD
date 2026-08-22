from __future__ import annotations

from pathlib import Path

import pytest

from elia.config import BrainConfig, ExecutiveConfig, RuntimeConfig, load_config


def _brain(**overrides: object) -> BrainConfig:
    values: dict[str, object] = {
        "backend": "mock",
        "model_id": "mock",
        "base_url": "http://127.0.0.1:8000/v1",
        "timeout_seconds": 5.0,
        "max_tokens": 512,
        "temperature": 0.0,
        "top_p": 1.0,
        "thinking": False,
    }
    values.update(overrides)
    return BrainConfig(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", 0.0),
        ("max_tokens", 0),
        ("max_tokens", 1.5),
        ("temperature", float("inf")),
        ("temperature", -0.01),
        ("top_p", 0.0),
        ("top_p", 1.01),
    ],
)
def test_brain_config_rejects_invalid_numeric_limits(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        _brain(**{field: value})


def test_transformers_backend_requires_full_immutable_revision() -> None:
    with pytest.raises(ValueError, match="immutable model_revision"):
        _brain(backend="transformers_4bit", model_id="org/model")
    with pytest.raises(ValueError, match="full lowercase Git commit SHA"):
        _brain(
            backend="transformers_4bit",
            model_id="org/model",
            model_revision="main",
        )
    config = _brain(
        backend="transformers_4bit",
        model_id="org/model",
        model_revision="a" * 40,
    )
    assert config.model_revision == "a" * 40


@pytest.mark.parametrize(
    "overrides",
    [
        {"cycle_sleep_seconds": float("nan")},
        {"cycle_sleep_seconds": -1.0},
        {"max_action_output_chars": 0},
        {"max_action_output_chars": 1.5},
        {"weekly_gpu_budget_hours": float("inf")},
        {"weekly_gpu_budget_hours": -1.0},
        {"memory_recall_limit": 0},
        {"memory_recall_limit": 1.5},
        {"max_in_session_sleep_seconds": -1.0},
    ],
)
def test_runtime_config_rejects_invalid_resource_limits(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    values: dict[str, object] = {
        "state_dir": tmp_path / "state",
        "cycle_sleep_seconds": 0.0,
        "max_action_output_chars": 16_000,
        "weekly_gpu_budget_hours": 30.0,
        "memory_recall_limit": 12,
    }
    values.update(overrides)
    with pytest.raises(ValueError):
        RuntimeConfig(**values)  # type: ignore[arg-type]


def test_auto_checkpoint_cannot_overwrite_live_state(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with pytest.raises(ValueError, match="outside runtime state_dir"):
        RuntimeConfig(
            state_dir=state,
            cycle_sleep_seconds=0,
            max_action_output_chars=16_000,
            weekly_gpu_budget_hours=30,
            memory_recall_limit=12,
            auto_checkpoint_path=state / "checkpoint",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"critical_need_threshold": float("nan")},
        {"maintenance_need_threshold": 0.9, "critical_need_threshold": 0.8},
        {"low_budget_ratio": 0.6, "deep_budget_ratio": 0.5},
        {"low_tokens": 16},
        {"low_tokens": 32.5},
        {"low_tokens": 640, "normal_tokens": 256},
        {"deep_target_brain_seconds": -1},
    ],
)
def test_executive_config_fails_closed_before_runtime(
    overrides: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        ExecutiveConfig(**overrides)  # type: ignore[arg-type]


def test_load_config_rejects_nonfinite_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("ELIA_WEEKLY_GPU_HOURS", "nan")
    with pytest.raises(ValueError, match="must be finite"):
        load_config(root / "config" / "genesis.yaml")
