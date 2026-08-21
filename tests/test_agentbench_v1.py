from __future__ import annotations

from pathlib import Path

from elia.agentbench import run_agentbench


def test_agentbench_adversarial_and_long_horizon_baseline(tmp_path: Path) -> None:
    report = run_agentbench(tmp_path / "agentbench")
    assert report["all_passed"] is True, report
    assert report["pass_rate"] == 1.0
    assert report["total"] >= 19
    for category in (
        "memory",
        "external_effects",
        "recovery",
        "authority",
        "long_horizon",
        "provider_boundary",
        "policy",
        "architecture",
    ):
        assert report["categories"][category]["pass_rate"] == 1.0
    long_horizon = next(
        item
        for item in report["scenarios"]
        if item["name"] == "commitment_64_generations"
    )
    assert long_horizon["metrics"]["generations"] == 64
