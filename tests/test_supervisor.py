from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from elia.identity import IdentityBundle
from elia.memory import MemoryStore
from elia.supervisor import ResidentSupervisor


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def make_config_copy(tmp_path: Path) -> Path:
    root = repo_root()
    original = (root / "config" / "genesis.yaml").read_text(encoding="utf-8")
    # Absolute artifact paths keep the copied config self-contained for the test.
    original = original.replace(
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
    path.write_text(original, encoding="utf-8")
    return path


def seed_identity_meta(supervisor: ResidentSupervisor) -> MemoryStore:
    memory = MemoryStore(supervisor.config.runtime.state_dir / "memory.sqlite3")
    memory.set_meta("identity_bundle_fingerprint", supervisor.identity.fingerprint)
    memory.set_meta("branch_id", supervisor.config.branch_id)
    return memory


def test_supervisor_sleeps_when_next_wake_is_in_future(tmp_path: Path) -> None:
    supervisor = ResidentSupervisor(make_config_copy(tmp_path), heartbeat_seconds=10, max_cycles=3)
    memory = seed_identity_meta(supervisor)
    memory.set_meta("next_wake_at", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    decision = supervisor.decide()
    assert decision.action == "sleep"
    assert decision.child_command is None


def test_supervisor_constructs_fixed_child_only_when_due(tmp_path: Path) -> None:
    supervisor = ResidentSupervisor(make_config_copy(tmp_path), heartbeat_seconds=10, max_cycles=3)
    memory = seed_identity_meta(supervisor)
    memory.set_meta("next_wake_at", (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    decision = supervisor.decide()
    assert decision.action == "launch"
    assert decision.child_command is not None
    command = list(decision.child_command)
    assert command[1:3] == ["-m", "elia"]
    assert "--cycles" in command
    assert command[-1] == "3"


def test_supervisor_halts_on_identity_mismatch_even_if_due(tmp_path: Path) -> None:
    supervisor = ResidentSupervisor(make_config_copy(tmp_path))
    memory = MemoryStore(supervisor.config.runtime.state_dir / "memory.sqlite3")
    memory.set_meta("identity_bundle_fingerprint", "0" * 64)
    memory.set_meta("branch_id", supervisor.config.branch_id)
    memory.set_meta("next_wake_at", datetime.now(timezone.utc).isoformat())
    decision = supervisor.decide()
    assert decision.action == "halt"
    assert "Identity fingerprint mismatch" in decision.reason
