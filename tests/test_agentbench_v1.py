from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys

from elia.agentbench import run_agentbench


def test_agentbench_deterministic_invariant_regression_suite(tmp_path: Path) -> None:
    report = run_agentbench(tmp_path / "agentbench")
    assert report["all_passed"] is True, report
    assert report["pass_rate"] == 1.0
    assert report["total"] >= 19
    for category in (
        "memory",
        "external_effects",
        "recovery",
        "authority",
        "persistence_regression",
        "provider_boundary",
        "policy",
        "architecture",
    ):
        assert report["categories"][category]["pass_rate"] == 1.0
    persistence = next(
        item
        for item in report["scenarios"]
        if item["name"] == "commitment_64_store_reopens"
    )
    assert persistence["metrics"]["generations"] == 64
    assert report["suite"] == "ELIA deterministic invariant regression suite"
    assert len(report["run_manifest"]["scenario_manifest_sha256"]) == 64
    assert len(report["run_manifest"]["source_manifest_sha256"]) == 64


def test_agentbench_contains_no_optimization_removable_asserts() -> None:
    source = Path(__file__).resolve().parents[1] / "elia" / "agentbench.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))


def test_agentbench_checks_still_execute_in_optimized_interpreter(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-O", "-m", "elia.agentbench", "--json"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["all_passed"] is True
    assert report["run_manifest"]["optimized_interpreter"] is True
