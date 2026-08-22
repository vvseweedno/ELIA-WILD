from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from elia.bootstrap import bootstrap
from elia.branching import BranchManager
from elia.config import load_config
from elia.identity import IdentityStore
from elia.memory import MemoryStore


def _config_path(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
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
        "epistemic_registry: epistemic.yaml",
        f"epistemic_registry: {root / 'config' / 'epistemic.yaml'}",
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


def test_failed_fork_restores_branch_lineage_chronicle_and_active_crc(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _config_path(tmp_path)
    monkeypatch.delenv("ELIA_BRANCH_ID", raising=False)
    config = load_config(config_path)
    assert bootstrap(config, cycles=1)["ok"] is True

    manager = BranchManager(config)
    baseline = config.runtime.state_dir / "workspace" / ".organism" / "last-healthy-crc.json"
    assert baseline.is_file()
    chronicle_head = manager.chronicle.head()
    lineage_head = manager.identity_store.last_lineage()
    assert lineage_head is not None

    original_append = manager.chronicle.append

    def fail_fork_append(kind, payload):
        if kind == "BRANCH_FORK":
            raise RuntimeError("simulated fork Chronicle failure")
        return original_append(kind, payload)

    monkeypatch.setattr(manager.chronicle, "append", fail_fork_append)
    with pytest.raises(RuntimeError, match="simulated fork Chronicle failure"):
        manager.fork("child-failed", note="fault injection")

    memory = MemoryStore(config.runtime.state_dir / "memory.sqlite3")
    identity_store = IdentityStore(config.runtime.state_dir / "memory.sqlite3")
    assert memory.get_meta("branch_id") == "main"
    assert identity_store.last_lineage().id == lineage_head.id
    assert identity_store.last_lineage().branch_id == "main"
    assert manager.chronicle.head() == chronicle_head
    assert baseline.is_file()


def test_branch_manager_recovers_checkpoint_swap_before_store_construction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _config_path(tmp_path)
    monkeypatch.delenv("ELIA_BRANCH_ID", raising=False)
    config = load_config(config_path)
    assert bootstrap(config, cycles=1)["ok"] is True
    state = config.runtime.state_dir.resolve()
    staging = state.parent / f".{state.name}.restore-interrupted"
    backup = state.parent / f".{state.name}.backup-interrupted"
    shutil.copytree(state, staging)
    MemoryStore(staging / "memory.sqlite3").set_meta(
        "branch_id", "dirty-unpublished"
    )
    state.rename(backup)
    control = state.parent / f".{state.name}.checkpoint-control"
    control.mkdir()
    journal = control / "restore.json"
    journal.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "state_dir": str(state),
                "staging": str(staging),
                "backup": str(backup),
                "had_original": True,
                "status": "old_moved",
                "counter": 1,
                "digest": "a" * 64,
            }
        ),
        encoding="utf-8",
    )

    manager = BranchManager(config)

    assert manager.memory.get_meta("branch_id") == "main"
    assert state.is_dir()
    assert not journal.exists()
    assert not staging.exists()
    assert not backup.exists()
