from __future__ import annotations

from pathlib import Path

from elia.config import load_config
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
