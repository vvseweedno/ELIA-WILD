from __future__ import annotations

from pathlib import Path

import yaml


def test_scheduled_wake_is_guarded_bounded_and_secret_scoped() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflow_path = repo_root / ".github" / "workflows" / "wake.yml"
    text = workflow_path.read_text(encoding="utf-8")

    assert "vars.ELIA_WAKE_ENABLED == 'true'" in text
    assert "ELIA_KAGGLE_KERNEL_TIMEOUT: ${{ vars.ELIA_KAGGLE_KERNEL_TIMEOUT }}" in text
    assert "kaggle==2.2.4" in text
    assert "concurrency:" in text
    assert "cancel-in-progress: false" in text
    assert "timeout-minutes: 15" in text
    assert "persist-credentials: false" in text
    assert "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09" in text
    assert "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1" in text

    parsed = yaml.safe_load(text)
    heartbeat = parsed["jobs"]["heartbeat"]
    assert heartbeat["if"] == "${{ vars.ELIA_WAKE_ENABLED == 'true' }}"
    assert heartbeat["timeout-minutes"] == 15
    assert parsed["permissions"]["contents"] == "read"
    assert parsed["permissions"]["actions"] == "read"

    # Continuity/Kaggle credentials must not exist job-wide, where checkout/setup/install
    # code could read them. Only the dedicated trusted steps receive the minimum secret.
    assert "KAGGLE_API_TOKEN" not in heartbeat.get("env", {})
    assert "ELIA_CHECKPOINT_KEY" not in heartbeat.get("env", {})
    assert "ELIA_CHECKPOINT_ENCRYPTION_KEY" not in heartbeat.get("env", {})
    assert "ELIA_WAKE_TRUST_ANCHOR_SEED_B64" not in heartbeat.get("env", {})

    steps = {step["name"]: step for step in heartbeat["steps"]}
    assert steps["Heartbeat"]["env"]["KAGGLE_API_TOKEN"] == "${{ secrets.KAGGLE_API_TOKEN }}"
    assert steps["Heartbeat"]["env"]["ELIA_CHECKPOINT_KEY"] == "${{ secrets.ELIA_CHECKPOINT_KEY }}"
    assert (
        steps["Heartbeat"]["env"]["ELIA_CHECKPOINT_ENCRYPTION_KEY"]
        == "${{ secrets.ELIA_CHECKPOINT_ENCRYPTION_KEY }}"
    )
    restore_env = steps["Restore durable wake witness"]["env"]
    assert restore_env["GH_TOKEN"] == "${{ github.token }}"
    assert (
        restore_env["ELIA_WAKE_TRUST_ANCHOR_SEED_B64"]
        == "${{ secrets.ELIA_WAKE_TRUST_ANCHOR_SEED_B64 }}"
    )
    assert "KAGGLE_API_TOKEN" not in steps["Install wake transport"].get("env", {})
    assert "ELIA_CHECKPOINT_KEY" not in steps["Install wake transport"].get("env", {})


def test_wake_witness_survives_ephemeral_runner_and_never_scheduled_bootstraps() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    text = (repo_root / ".github" / "workflows" / "wake.yml").read_text(encoding="utf-8")

    assert "elia-wake-trust-anchor" in text
    assert "gh api --method GET" in text
    assert "gh run download" in text
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in text
    assert "retention-days: 7" in text
    assert "initialize_anchor:" in text
    assert "github.event_name == 'workflow_dispatch'" in text
    assert "No durable wake witness exists. Refusing scheduled bootstrap from Kaggle state." in text
    assert "ELIA_KAGGLE_TRUST_ANCHOR: ${{ runner.temp }}/elia-wake-anchor/kaggle-trust-anchor.json" in text


def test_wake_schedule_is_hourly_not_continuous() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    text = (repo_root / ".github" / "workflows" / "wake.yml").read_text(encoding="utf-8")
    assert "cron: '17 * * * *'" in text
    assert "while true" not in text.lower()
