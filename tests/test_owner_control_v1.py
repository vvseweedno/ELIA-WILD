from __future__ import annotations

from pathlib import Path

import pytest

from elia.owner_control import (
    DelegationLeaseExpired,
    DelegationRevoked,
    HumanApprovalRequired,
    OwnerControl,
    OwnerKillSwitch,
    OwnerMandate,
)


def _control(tmp_path: Path, *, require_lease: bool = True) -> OwnerControl:
    mandate = OwnerMandate(
        schema_version=1,
        precedence=("owner", "mission", "continuity"),
        require_external_lease=require_lease,
        approval_required_actions=("submit_work",),
        default_lease_hours=1.0,
        fingerprint="f" * 64,
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
