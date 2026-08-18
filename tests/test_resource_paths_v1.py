from __future__ import annotations

from pathlib import Path
import shutil

from elia.config import load_config
import elia.paths as paths


ROOT = Path(__file__).resolve().parents[1]


def test_source_checkout_default_resources_are_cwd_independent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    resolved = paths.resolve_config_entry("config/genesis.yaml")
    assert resolved == (ROOT / "config" / "genesis.yaml").resolve()
    config = load_config()
    assert config.subject_core_path == (ROOT / "config" / "subject_core.yaml").resolve()
    assert config.skills_dir == (ROOT / "skills").resolve()


def test_installed_resource_root_keeps_immutable_resources_out_of_runtime_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    share = tmp_path / "prefix" / "share" / "elia-wild"
    shutil.copytree(ROOT / "config", share / "config")
    shutil.copytree(ROOT / "skills", share / "skills")
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    monkeypatch.setattr(paths, "source_resource_root", lambda: tmp_path / "missing-source")
    monkeypatch.setattr(paths, "installed_resource_root", lambda: share)
    monkeypatch.chdir(run_dir)

    assert paths.default_resource_root() == share
    assert paths.resolve_config_entry("config/genesis.yaml") == share / "config" / "genesis.yaml"

    config = load_config()
    assert config.subject_core_path == share / "config" / "subject_core.yaml"
    assert config.continuity_constitution_path == share / "config" / "continuity_constitution.yaml"
    assert config.system_prompt_path == share / "config" / "system_prompt.md"
    assert config.epistemic_path == share / "config" / "epistemic.yaml"
    assert config.skills_dir == share / "skills"
    assert config.runtime.state_dir == run_dir / ".elia"


def test_explicit_external_config_still_wins_over_builtin_default(monkeypatch, tmp_path: Path) -> None:
    external_root = tmp_path / "external"
    external_config = external_root / "config" / "genesis.yaml"
    external_config.parent.mkdir(parents=True)
    text = (ROOT / "config" / "genesis.yaml").read_text(encoding="utf-8")
    text = text.replace(
        "subject_core: subject_core.yaml",
        f"subject_core: {ROOT / 'config' / 'subject_core.yaml'}",
    ).replace(
        "continuity_constitution: continuity_constitution.yaml",
        f"continuity_constitution: {ROOT / 'config' / 'continuity_constitution.yaml'}",
    ).replace(
        "system_prompt: system_prompt.md",
        f"system_prompt: {ROOT / 'config' / 'system_prompt.md'}",
    ).replace(
        "epistemic_registry: epistemic.yaml",
        f"epistemic_registry: {ROOT / 'config' / 'epistemic.yaml'}",
    ).replace(
        "skills_dir: skills",
        f"skills_dir: {ROOT / 'skills'}",
    )
    external_config.write_text(text, encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    config = load_config(external_config)
    assert paths.resolve_config_entry(external_config) == external_config.resolve()
    assert config.runtime.state_dir == external_root / ".elia"
