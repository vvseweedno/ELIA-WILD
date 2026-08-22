from __future__ import annotations

import json
import os
from pathlib import Path

from elia.chronicle import Chronicle
from elia.config import load_config
from elia.memory import MemoryStore
from elia.vitals import VitalSigns


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def config_for(tmp_path: Path):
    source = (repo_root() / "config" / "genesis.yaml").read_text(encoding="utf-8")
    source = source.replace(
        "subject_core: subject_core.yaml",
        f"subject_core: {repo_root() / 'config' / 'subject_core.yaml'}",
    ).replace(
        "continuity_constitution: continuity_constitution.yaml",
        f"continuity_constitution: {repo_root() / 'config' / 'continuity_constitution.yaml'}",
    ).replace(
        "system_prompt: system_prompt.md",
        f"system_prompt: {repo_root() / 'config' / 'system_prompt.md'}",
    ).replace(
        "skills_dir: skills",
        f"skills_dir: {repo_root() / 'skills'}",
    ).replace(
        "state_dir: .elia",
        f"state_dir: {tmp_path / '.elia'}",
    )
    path = tmp_path / "genesis.yaml"
    path.write_text(source, encoding="utf-8")
    return load_config(path)


def test_vitals_preserve_last_healthy_crc(tmp_path: Path) -> None:
    monitor = VitalSigns(config_for(tmp_path))
    first = monitor.check(persist=True)
    assert first.healthy is True
    baseline = Path(first.last_healthy_crc_path)
    assert baseline.is_file()
    before = baseline.read_text(encoding="utf-8")

    second = monitor.check(persist=True)
    assert second.healthy is True
    assert baseline.is_file()
    assert second.failure_evidence_path is None
    assert "identity_fingerprint" in baseline.read_text(encoding="utf-8")
    assert before != ""


def test_vitals_are_model_independent(tmp_path: Path) -> None:
    monitor = VitalSigns(config_for(tmp_path))
    report = monitor.check(persist=False)
    assert report.healthy is True
    assert report.organism["healthy"] is True
    assert report.crc["identity_id"] == "elia-wild"
    assert "prototype" in report.research_maturity


def test_vitals_recovers_interrupted_checkpoint_restore_before_opening_stores(
    tmp_path: Path,
) -> None:
    config = config_for(tmp_path)
    state = config.runtime.state_dir
    memory = MemoryStore(state / "memory.sqlite3")
    memory.set_meta("branch_id", "accepted-before-restore-crash")
    Chronicle(state / "chronicle.jsonl").append("BOOT", {"accepted": True})

    backup = state.parent / f".{state.name}.backup-test"
    staging = state.parent / f".{state.name}.restore-test"
    os.replace(state, backup)
    staging.mkdir()
    (staging / "unaccepted.txt").write_text("replacement", encoding="utf-8")
    control = state.parent / f".{state.name}.checkpoint-control"
    control.mkdir()
    (control / "restore.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "state_dir": str(state.resolve()),
                "staging": str(staging.resolve()),
                "backup": str(backup.resolve()),
                "had_original": True,
                "status": "old_moved",
            }
        ),
        encoding="utf-8",
    )

    monitor = VitalSigns(config)

    assert monitor.checkpoint_restore_recovered is True
    assert monitor.transition_recovery.recovered is False
    assert MemoryStore(state / "memory.sqlite3").get_meta("branch_id") == (
        "accepted-before-restore-crash"
    )
    assert not staging.exists()
    assert not (control / "restore.json").exists()
