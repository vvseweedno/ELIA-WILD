from __future__ import annotations

from pathlib import Path

from elia.bootstrap import bootstrap
from elia.branching import BranchManager
from elia.config import load_config
from elia.identity import IdentityStore
from elia.vitals import VitalSigns


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def make_config(tmp_path: Path) -> Path:
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
    return path


def test_lineage_accepts_explicit_fork_but_rejects_silent_jump(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    store = IdentityStore(db)
    fp = "f" * 64
    store.record_lineage(
        event="boot", branch_id="main", body_version="1", brain_backend="mock", model_id="m", identity_fingerprint=fp
    )
    store.record_lineage(
        event="fork", branch_id="child", body_version="1", brain_backend="mock", model_id="m", identity_fingerprint=fp, note="explicit fork"
    )
    store.record_lineage(
        event="boot", branch_id="child", body_version="1", brain_backend="mock", model_id="m", identity_fingerprint=fp
    )
    assert store.verify_lineage(expected_identity_fingerprint=fp, expected_branch_id="child") == (True, None)

    bad = IdentityStore(tmp_path / "bad.sqlite3")
    bad.record_lineage(
        event="boot", branch_id="main", body_version="1", brain_backend="mock", model_id="m", identity_fingerprint=fp
    )
    bad.record_lineage(
        event="boot", branch_id="child", body_version="1", brain_backend="mock", model_id="m", identity_fingerprint=fp
    )
    valid, error = bad.verify_lineage(expected_identity_fingerprint=fp, expected_branch_id="child")
    assert valid is False
    assert "without explicit fork" in str(error)


def test_branch_manager_preserves_parent_crc_and_child_persists(tmp_path: Path, monkeypatch) -> None:
    config_path = make_config(tmp_path)
    monkeypatch.delenv("ELIA_BRANCH_ID", raising=False)
    config = load_config(config_path)
    boot = bootstrap(config, cycles=1)
    assert boot["ok"] is True
    baseline = config.runtime.state_dir / "workspace" / ".organism" / "last-healthy-crc.json"
    assert baseline.is_file()

    report = BranchManager(config).fork(
        "child-a",
        note="controlled branch test",
    )
    assert report.from_branch == "main"
    assert report.to_branch == "child-a"
    assert report.archived_crc_path is not None
    assert Path(report.archived_crc_path).is_file()
    assert not baseline.exists()

    reloaded = load_config(config_path)
    assert reloaded.branch_id == "child-a"
    vitals = VitalSigns(reloaded).check(persist=True)
    assert vitals.healthy is True
    assert vitals.crc["branch_id"] == "child-a"
    assert vitals.continuity_comparison is None
