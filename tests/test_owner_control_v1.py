from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import sqlite3
from threading import Barrier, Thread
import time

import pytest

from elia.owner_control import (
    DelegationLeaseExpired,
    DelegationRevoked,
    HumanApprovalRequired,
    OwnerControl,
    OwnerKillSwitch,
    OwnerMandate,
    arguments_fingerprint,
    owner_kill_active,
    owner_signal_path,
)
from elia.memory import MemoryStore
from elia.transition_kernel import StateWriterLock


def _owner_mandate() -> OwnerMandate:
    return OwnerMandate(
        schema_version=1,
        precedence=("owner", "mission", "continuity"),
        require_external_lease=True,
        approval_required_actions=("submit_work",),
        default_lease_hours=1.0,
        fingerprint="f" * 64,
    )


def _set_kill_in_process(database: Path, output: multiprocessing.Queue) -> None:
    try:
        OwnerControl(database, _owner_mandate()).kill(reason="concurrent owner stop")
    except BaseException as exc:
        output.put(f"error:{type(exc).__name__}:{exc}")
    else:
        output.put("completed")


def _control(tmp_path: Path, *, require_lease: bool = True) -> OwnerControl:
    mandate = _owner_mandate()
    if not require_lease:
        mandate = OwnerMandate(
            schema_version=mandate.schema_version,
            precedence=mandate.precedence,
            require_external_lease=False,
            approval_required_actions=mandate.approval_required_actions,
            default_lease_hours=mandate.default_lease_hours,
            fingerprint=mandate.fingerprint,
        )
    return OwnerControl(tmp_path / "memory.sqlite3", mandate)


def test_owner_kill_preempts_runtime(tmp_path: Path) -> None:
    control = _control(tmp_path)
    control.kill(reason="operator stop")
    with pytest.raises(OwnerKillSwitch):
        control.assert_runtime_allowed()


def test_external_authority_requires_active_lease_and_respects_revocation(tmp_path: Path) -> None:
    control = _control(tmp_path)
    with pytest.raises(DelegationLeaseExpired):
        control.assert_external_authorized("browser_click", {"selector": "#ok"})

    control.grant_lease(approved_by="owner", hours=1, evidence="bounded test lease")
    control.assert_external_authorized("browser_click", {"selector": "#ok"})

    control.revoke(reason="operator revoked external body")
    with pytest.raises(DelegationRevoked):
        control.assert_external_authorized("browser_click", {"selector": "#ok"})


def test_one_time_human_approval_is_exact_and_consumed(tmp_path: Path) -> None:
    control = _control(tmp_path)
    control.grant_lease(approved_by="owner", hours=1, evidence="work-port test")
    args = {"port": "configured", "work_item_id": 7}

    with pytest.raises(HumanApprovalRequired):
        control.assert_external_authorized("submit_work", args)

    control.approve_once(
        "submit_work",
        args,
        approved_by="owner",
        evidence="reviewed exact staged artifact",
        ttl_seconds=60,
    )
    control.assert_external_authorized("submit_work", args)

    with pytest.raises(HumanApprovalRequired):
        control.assert_external_authorized("submit_work", args)
    with pytest.raises(HumanApprovalRequired):
        control.assert_external_authorized(
            "submit_work", {"port": "configured", "work_item_id": 8}
        )


def test_missing_owner_mandate_fails_closed_for_external_effects(tmp_path: Path) -> None:
    mandate = OwnerMandate.load(tmp_path / "missing-owner-mandate.yaml", required=False)
    assert mandate.require_external_lease is True
    control = OwnerControl(tmp_path / "fallback-memory.sqlite3", mandate)
    with pytest.raises(DelegationLeaseExpired):
        control.assert_external_authorized("browser_click", {"selector": "#ok"})


def test_kill_signal_survives_atomic_state_directory_replacement(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".elia"
    control = _control(state)
    control.kill(reason="stop before restore")
    signal = owner_signal_path(state / "memory.sqlite3")
    assert signal.parent == tmp_path
    assert signal.stat().st_mode & 0o777 == 0o600

    state.rename(tmp_path / "old-state")
    MemoryStore(state / "memory.sqlite3")
    restored_control = _control(state)

    assert restored_control.snapshot()["killed"] is True
    assert owner_kill_active(state / "memory.sqlite3") is True


def test_corrupt_external_owner_signal_fails_closed(tmp_path: Path) -> None:
    state = tmp_path / ".elia"
    control = _control(state)
    signal = owner_signal_path(control.path)
    signal.write_text("not-json", encoding="utf-8")

    assert owner_kill_active(control.path) is True
    assert control.snapshot()["killed"] is True


def test_positive_kill_releases_signal_before_waiting_for_constructor_writer(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".elia"
    control = _control(state)
    context = multiprocessing.get_context("fork")
    output = context.Queue()
    writer = StateWriterLock(state)
    writer.acquire()
    process = context.Process(target=_set_kill_in_process, args=(control.path, output))
    process.start()
    signal = owner_signal_path(control.path)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            if json.loads(signal.read_text(encoding="utf-8")).get("killed") is True:
                break
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(0.01)
    else:
        writer.release()
        process.terminate()
        process.join(timeout=2)
        pytest.fail("concurrent kill did not publish its fail-safe signal")

    constructed: list[OwnerControl] = []
    constructor = Thread(
        target=lambda: constructed.append(OwnerControl(control.path, _owner_mandate())),
        daemon=True,
    )
    constructor.start()
    constructor.join(timeout=1.0)
    completed_while_writer_held = not constructor.is_alive()
    writer.release()
    constructor.join(timeout=2.0)
    process.join(timeout=5.0)

    assert completed_while_writer_held is True
    assert not constructor.is_alive()
    assert process.exitcode == 0
    assert output.get(timeout=1) == "completed"
    assert OwnerControl(control.path, _owner_mandate()).snapshot()["killed"] is True


def test_concurrent_kill_and_clear_leave_sidecar_and_database_consistent(
    tmp_path: Path,
) -> None:
    control = _control(tmp_path / ".elia")
    control.kill(reason="initial stop")
    barrier = Barrier(3)
    errors: list[BaseException] = []

    def mutate(value: bool) -> None:
        try:
            barrier.wait(timeout=2)
            control.kill(reason=f"concurrent killed={value}", killed=value)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        Thread(target=mutate, args=(False,), daemon=True),
        Thread(target=mutate, args=(True,), daemon=True),
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []

    signal_killed = bool(
        json.loads(owner_signal_path(control.path).read_text(encoding="utf-8"))["killed"]
    )
    with sqlite3.connect(control.path) as conn:
        database_killed = bool(
            conn.execute(
                "SELECT killed FROM owner_control_state WHERE singleton=1"
            ).fetchone()[0]
        )
    assert signal_killed is database_killed


def test_approval_fingerprint_is_canonical_and_rejects_non_json_values(
    tmp_path: Path,
) -> None:
    first = arguments_fingerprint(
        "submit_work", {"port": "configured", "nested": {"a": 1, "b": 2}}
    )
    second = arguments_fingerprint(
        "submit_work", {"nested": {"b": 2, "a": 1}, "port": "configured"}
    )
    assert first == second

    class Stringifiable:
        def __str__(self) -> str:
            return "deceptively-stable"

    for invalid in ({"amount": float("nan")}, {"value": Stringifiable()}):
        with pytest.raises(ValueError):
            arguments_fingerprint("submit_work", invalid)
        control = _control(tmp_path / f"invalid-{len(str(invalid))}")
        with pytest.raises(ValueError):
            control.approve_once(
                "submit_work",
                invalid,
                approved_by="owner",
                evidence="must never be stored",
            )
        with sqlite3.connect(control.path) as conn:
            count = int(conn.execute("SELECT COUNT(*) FROM human_approvals").fetchone()[0])
        assert count == 0
