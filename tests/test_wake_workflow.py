from __future__ import annotations

from pathlib import Path

import yaml


def test_scheduled_wake_is_guarded_and_bounded() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflow_path = repo_root / ".github" / "workflows" / "wake.yml"
    text = workflow_path.read_text(encoding="utf-8")

    assert "vars.ELIA_WAKE_ENABLED == 'true'" in text
    assert "KAGGLE_API_TOKEN: ${{ secrets.KAGGLE_API_TOKEN }}" in text
    assert "ELIA_CHECKPOINT_KEY: ${{ secrets.ELIA_CHECKPOINT_KEY }}" in text
    assert "ELIA_KAGGLE_KERNEL_TIMEOUT: ${{ vars.ELIA_KAGGLE_KERNEL_TIMEOUT }}" in text
    assert "kaggle>=2.2.4,<3" in text
    assert "concurrency:" in text
    assert "cancel-in-progress: false" in text
    assert "timeout-minutes: 15" in text

    parsed = yaml.safe_load(text)
    jobs = parsed["jobs"]
    heartbeat = jobs["heartbeat"]
    assert heartbeat["if"] == "${{ vars.ELIA_WAKE_ENABLED == 'true' }}"
    assert heartbeat["timeout-minutes"] == 15


def test_wake_schedule_is_hourly_not_continuous() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    text = (repo_root / ".github" / "workflows" / "wake.yml").read_text(encoding="utf-8")
    assert "cron: '17 * * * *'" in text
    assert "while true" not in text.lower()
