from __future__ import annotations

import json
from pathlib import Path
import runpy

from elia.wake_transport import render_runner


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def relay_module() -> dict:
    return runpy.run_path(str(repo_root() / "scripts" / "kaggle_wake.py"))


def test_kaggle_cli_child_never_receives_continuity_secrets(monkeypatch) -> None:
    module = relay_module()
    monkeypatch.setenv("KAGGLE_API_TOKEN", "kaggle-token")
    monkeypatch.setenv("ELIA_CHECKPOINT_KEY", "auth-secret")
    monkeypatch.setenv("ELIA_CHECKPOINT_ENCRYPTION_KEY", "encryption-secret")
    monkeypatch.setenv("ELIA_CHECKPOINT_REQUIRE_ENCRYPTION", "1")

    env = module["_kaggle_child_env"]()

    assert env["KAGGLE_API_TOKEN"] == "kaggle-token"
    assert "ELIA_CHECKPOINT_KEY" not in env
    assert "ELIA_CHECKPOINT_ENCRYPTION_KEY" not in env
    assert "ELIA_CHECKPOINT_REQUIRE_ENCRYPTION" not in env


def test_rendered_remote_runner_requires_encrypted_continuity() -> None:
    template = (repo_root() / "runtime" / "kaggle" / "runner_template.py").read_text(
        encoding="utf-8"
    )
    rendered = render_runner(
        template,
        {
            "version": 1,
            "launch_nonce": "n" * 32,
            "source_digest": "a" * 64,
            "repo_url": "https://github.com/vvseweedno/ELIA-WILD.git",
            "repo_ref": "elia/genesis-1.7.1-consolidation",
            "max_cycles": 2,
        },
    )

    compile(rendered, "elia_wild_runner.py", "exec")
    assert "ELIA_CHECKPOINT_KEY" in rendered
    assert "ELIA_CHECKPOINT_ENCRYPTION_KEY" in rendered
    assert "ELIA_CHECKPOINT_REQUIRE_ENCRYPTION" in rendered
    assert "assert_encrypted_checkpoint(source_checkpoint)" in rendered
    assert '"encrypted_checkpoint": True' in rendered


def test_prepared_kernel_is_private_t4_and_uses_current_kaggle_cli_flag(
    tmp_path: Path,
) -> None:
    module = relay_module()
    destination = tmp_path / "kernel"
    module["prepare_kernel"](
        repo_root=repo_root(),
        destination=destination,
        kernel_id="owner/elia-wild-runtime",
        state_dataset="owner/elia-wild-state",
        accelerator="NvidiaTeslaT4",
        source_digest="b" * 64,
        nonce="c" * 32,
        repo_ref="elia/genesis-1.7.1-consolidation",
        max_cycles=4,
    )

    metadata = json.loads(
        (destination / "kernel-metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["is_private"] == "true"
    assert metadata["enable_gpu"] == "true"
    assert metadata["machine_shape"] == "NvidiaTeslaT4"
    assert metadata["dataset_sources"] == ["owner/elia-wild-state"]

    source = (repo_root() / "scripts" / "kaggle_wake.py").read_text(encoding="utf-8")
    assert source.count('"--acc"') == 1
    assert source.count('"--timeout"') == 1
