from __future__ import annotations

from pathlib import Path

from elia.bootstrap import bootstrap
from elia.config import load_config
from elia.doctor import OrganismDoctor


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def make_config(tmp_path: Path):
    root = repo_root()
    text = (root / "config" / "genesis.yaml").read_text(encoding="utf-8")
    text = text.replace(
        "subject_core: subject_core.yaml",
        f"subject_core: {root / 'config' / 'subject_core.yaml'}",
    ).replace(
        "continuity_constitution: continuity_constitution.yaml",
        f"continuity_constitution: {root / 'config' / 'continuity_constitution.yaml'}",
    ).replace(
        "system_prompt: system_prompt.md",
        f"system_prompt: {root / 'config' / 'system_prompt.md'}",
    ).replace(
        "skills_dir: skills",
        f"skills_dir: {root / 'skills'}",
    ).replace(
        "state_dir: .elia",
        f"state_dir: {tmp_path / '.elia'}",
    )
    path = tmp_path / "genesis.yaml"
    path.write_text(text, encoding="utf-8")
    return load_config(path)


def test_doctor_is_cpu_only_and_reports_healthy_core(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    report = OrganismDoctor(config).run()
    assert report.healthy is True
    assert report.identity["identity_id"] == "elia-wild"
    assert report.organism["healthy"] is True
    assert report.capabilities["count"] >= 1
    assert report.skills["count"] >= 1


def test_bootstrap_uses_mock_brain_and_leaves_healthy_state(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    result = bootstrap(config, cycles=1)
    assert result["ok"] is True
    assert result["brain_backend_used"] == "mock"
    assert result["configured_brain_backend_not_loaded"] == "transformers_4bit"
    assert result["vitals"]["healthy"] is True
