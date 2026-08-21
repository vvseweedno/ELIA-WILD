from __future__ import annotations

from pathlib import Path

from elia.agentbench import run_agentbench


def test_agentbench_adversarial_and_long_horizon_baseline(tmp_path: Path) -> None:
    report = run_agentbench(tmp_path / "agentbench")
    assert report["all_passed"] is True, report
    assert report["pass_rate"] == 1.0
    assert report["total"] >= 8
    long_horizon = next(
        item
        for item in report["scenarios"]
        if item["name"] == "long_horizon_commitment_64_generations"
    )
    assert long_horizon["metrics"]["generations"] == 64
