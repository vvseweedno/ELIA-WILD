from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_command_redacts_checkpoint_key(monkeypatch, module: ModuleType) -> None:
    monkeypatch.setenv("ELIA_CHECKPOINT_KEY", "identity-hmac-secret")
    monkeypatch.setenv("KAGGLE_API_TOKEN", "kaggle-token")
    captured = {}

    class Completed:
        returncode = 0
        stdout = "ok"

    def fake_run(args, **kwargs):
        captured["args"] = list(args)
        captured["env"] = dict(kwargs.get("env") or {})
        return Completed()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.command(["kaggle", "datasets", "status", "owner/state"])
    assert getattr(result, "returncode", 0) == 0
    assert captured["env"]["KAGGLE_API_TOKEN"] == "kaggle-token"
    assert "ELIA_CHECKPOINT_KEY" not in captured["env"]


def test_wake_kaggle_child_does_not_receive_checkpoint_key(monkeypatch) -> None:
    module = _load("elia_test_kaggle_wake", ROOT / "scripts" / "kaggle_wake.py")
    _assert_command_redacts_checkpoint_key(monkeypatch, module)


def test_bootstrap_kaggle_child_does_not_receive_checkpoint_key(monkeypatch) -> None:
    module = _load(
        "elia_test_bootstrap_kaggle_state",
        ROOT / "scripts" / "bootstrap_kaggle_state.py",
    )
    _assert_command_redacts_checkpoint_key(monkeypatch, module)
