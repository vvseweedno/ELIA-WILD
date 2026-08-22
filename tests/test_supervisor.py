from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading

import pytest

from elia.memory import MemoryStore
from elia.owner_control import OwnerControl, OwnerMandate
from elia.supervisor import ResidentSupervisor, SupervisorAlreadyRunning


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


def test_supervisor_resolves_default_config_outside_checkout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = repo_root()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ELIA_STATE_DIR", str(tmp_path / ".elia"))

    supervisor = ResidentSupervisor(Path("config/genesis.yaml"))

    assert supervisor.config_path == (root / "config" / "genesis.yaml").resolve()
    assert supervisor.config.runtime.state_dir == (tmp_path / ".elia").resolve()


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


def test_supervisor_enforces_child_deadline_and_kills_process_group(
    tmp_path: Path,
) -> None:
    supervisor = ResidentSupervisor(
        make_config_copy(tmp_path),
        child_timeout_seconds=0.2,
        termination_grace_seconds=0.1,
    )
    result = supervisor.run_child(
        (sys.executable, "-c", "import time; time.sleep(30)")
    )
    assert result["timed_out"] is True
    assert result["terminated_by_owner"] is False
    assert result["returncode"] != 0


def test_owner_kill_terminates_running_cognitive_child(tmp_path: Path) -> None:
    supervisor = ResidentSupervisor(
        make_config_copy(tmp_path),
        child_timeout_seconds=10,
        termination_grace_seconds=0.1,
    )
    mandate = OwnerMandate(
        schema_version=1,
        precedence=("owner", "continuity"),
        require_external_lease=False,
        approval_required_actions=(),
        default_lease_hours=1.0,
        fingerprint="f" * 64,
    )
    control = OwnerControl(
        supervisor.config.runtime.state_dir / "memory.sqlite3", mandate
    )
    timer = threading.Timer(
        0.2, lambda: control.kill(reason="emergency stop during cognition")
    )
    timer.start()
    try:
        result = supervisor.run_child(
            (sys.executable, "-c", "import time; time.sleep(30)")
        )
    finally:
        timer.cancel()
        timer.join(timeout=1)
    assert result["terminated_by_owner"] is True
    assert result["timed_out"] is False
    assert result["returncode"] != 0


def test_supervisor_singleton_rejects_second_resident(tmp_path: Path) -> None:
    first = ResidentSupervisor(make_config_copy(tmp_path))
    second = ResidentSupervisor(tmp_path / "genesis.yaml")
    with first.singleton():
        with pytest.raises(SupervisorAlreadyRunning, match="another resident"):
            with second.singleton():
                raise AssertionError("second supervisor must never enter")
