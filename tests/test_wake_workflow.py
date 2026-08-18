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
    # Secrets must not exist in job-wide env, where checkout/setup/install code could read them.
    assert "KAGGLE_API_TOKEN" not in heartbeat.get("env", {})
    assert "ELIA_CHECKPOINT_KEY" not in heartbeat.get("env", {})
    steps = {step["name"]: step for step in heartbeat["steps"]}
    assert steps["Heartbeat"]["env"]["KAGGLE_API_TOKEN"] == "${{ secrets.KAGGLE_API_TOKEN }}"
    assert steps["Heartbeat"]["env"]["ELIA_CHECKPOINT_KEY"] == "${{ secrets.ELIA_CHECKPOINT_KEY }}"
    assert "KAGGLE_API_TOKEN" not in steps["Install wake transport"].get("env", {})
    assert "ELIA_CHECKPOINT_KEY" not in steps["Install wake transport"].get("env", {})


def test_wake_schedule_is_hourly_not_continuous() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    text = (repo_root / ".github" / "workflows" / "wake.yml").read_text(encoding="utf-8")
    assert "cron: '17 * * * *'" in text
    assert "while true" not in text.lower()
