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
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in text
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in text

    parsed = yaml.safe_load(text)
    heartbeat = parsed["jobs"]["heartbeat"]
    assert heartbeat["if"] == (
        "${{ vars.ELIA_WAKE_ENABLED == 'true' && "
        "github.ref_name == github.event.repository.default_branch }}"
    )
    assert heartbeat["timeout-minutes"] == 15
    assert parsed["permissions"]["contents"] == "read"
    assert parsed["permissions"]["actions"] == "read"

    # Continuity/Kaggle credentials must not exist job-wide, where checkout/setup/install
    # code could read them. Only the dedicated trusted steps receive the minimum secret.
    assert "KAGGLE_API_TOKEN" not in heartbeat.get("env", {})
    assert "ELIA_CHECKPOINT_KEY" not in heartbeat.get("env", {})
    assert "ELIA_CHECKPOINT_ENCRYPTION_KEY" not in heartbeat.get("env", {})
    assert "ELIA_WAKE_TRUST_ANCHOR_SEED_B64" not in heartbeat.get("env", {})
    assert "ELIA_WAKE_RESET_AUTH" not in heartbeat.get("env", {})

    steps = {step["name"]: step for step in heartbeat["steps"]}
    assert steps["Heartbeat"]["env"]["KAGGLE_API_TOKEN"] == "${{ secrets.KAGGLE_API_TOKEN }}"
    assert steps["Heartbeat"]["env"]["ELIA_CHECKPOINT_KEY"] == "${{ secrets.ELIA_CHECKPOINT_KEY }}"
    assert (
        steps["Heartbeat"]["env"]["ELIA_CHECKPOINT_ENCRYPTION_KEY"]
        == "${{ secrets.ELIA_CHECKPOINT_ENCRYPTION_KEY }}"
    )
    assert "inputs.reset_circuit == true" in (
        steps["Heartbeat"]["env"]["ELIA_WAKE_RESET_AUTH"]
    )
    assert steps["Heartbeat"]["continue-on-error"] is True
    restore_env = steps["Restore durable wake witness"]["env"]
    assert restore_env["GH_TOKEN"] == "${{ github.token }}"
    assert (
        restore_env["ELIA_WAKE_TRUST_ANCHOR_SEED_B64"]
        == "${{ secrets.ELIA_WAKE_TRUST_ANCHOR_SEED_B64 }}"
    )
    assert "KAGGLE_API_TOKEN" not in steps["Install wake transport"].get("env", {})
    assert "ELIA_CHECKPOINT_KEY" not in steps["Install wake transport"].get("env", {})
    assert steps["Persist durable wake witness"]["if"] == (
        "${{ always() && steps.restore.outcome == 'success' }}"
    )
    assert steps["Propagate heartbeat failure"]["if"] == (
        "${{ always() && steps.heartbeat.outcome == 'failure' }}"
    )


def test_wake_witness_survives_ephemeral_runner_and_never_scheduled_bootstraps() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    text = (repo_root / ".github" / "workflows" / "wake.yml").read_text(encoding="utf-8")

    assert "elia-wake-trust-anchor" in text
    assert "gh api --method GET" in text
    assert "actions/artifacts?name=elia-wake-trust-anchor" in text
    assert "workflow_run.head_branch == $branch" in text
    assert "workflow_run.head_repository_id == $repository_id" in text
    assert "actions/runs/${run_id}" in text
    assert "RUN_PATH" in text
    assert ".github/workflows/wake.yml" in text
    assert "actions/artifacts/${ARTIFACT_ID}/zip" in text
    assert "${#archive_entries[@]}" in text
    assert "${archive_entries[0]}" in text
    assert "size_in_bytes <= 65536" in text
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in text
    assert "retention-days: 90" in text
    assert "initialize_anchor:" in text
    assert "github.event_name == 'workflow_dispatch'" in text
    assert "No durable wake witness exists. Refusing scheduled bootstrap from Kaggle state." in text
    assert "ELIA_KAGGLE_TRUST_ANCHOR: ${{ runner.temp }}/elia-wake-anchor/kaggle-trust-anchor.json" in text
    assert "reset_circuit:" in text
    assert "ELIA_WAKE_RESET_REASON" in text


def test_wake_schedule_is_hourly_not_continuous() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    text = (repo_root / ".github" / "workflows" / "wake.yml").read_text(encoding="utf-8")
    assert "cron: '17 * * * *'" in text
    assert "while true" not in text.lower()
