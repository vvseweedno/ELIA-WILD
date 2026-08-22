from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

pytest.importorskip("mcp")
from mcp.server import MCPServer
from nacl.signing import SigningKey

from elia.economy import EconomyStore
from elia.observations import ObservationStore
from elia.resource_ecology import ResourceEcologyStore
from elia.resource_ingress_hardened import AttestedResourceIngressRegistry


VERIFY_ENV = "ELIA_TEST_ATTESTED_RESOURCE_KEY"
VERIFY_KEY = "0123456789abcdef0123456789abcdef"
PROVIDER_VERIFY_ENV = "ELIA_TEST_PROVIDER_VERIFY_KEY"
PROVIDER_SIGNING_KEY = SigningKey(bytes(range(32)))


ARGUMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "work_item_id": {"type": ["integer", "null"]},
        "asset": {"type": "string", "enum": ["cash"]},
        "unit": {"type": "string", "enum": ["USD"]},
    },
    "required": ["work_item_id", "asset", "unit"],
    "additionalProperties": False,
}


def _config() -> dict:
    return {
        "body": {
            "mcp": {
                "enabled": True,
                "servers": {
                    "bank": {
                        "enabled": True,
                        "allow_tool_calls": True,
                        "allowed_tools": ["observe_credit"],
                        "tool_argument_schemas": {"observe_credit": ARGUMENT_SCHEMA},
                        "allowed_resources": [],
                    }
                },
            }
        },
        "resource_ingress": {
            "enabled": True,
            "verifiers": {
                "bank_usd": {
                    "enabled": True,
                    "server": "bank",
                    "tool": "observe_credit",
                    "authority": "bank-usd-verifier",
                    "key_env": VERIFY_ENV,
                    "asset": "cash",
                    "unit": "USD",
                    "kind": "income",
                    "min_amount": 1,
                    "max_amount": 1000,
                    "target_amount_tolerance": 0,
                    "expected_provider": "test-bank",
                    "expected_account_binding": "acct-sha256:abc",
                    "provider_verify_key_env": PROVIDER_VERIFY_ENV,
                    "max_attestation_age_seconds": 3600,
                }
            },
        },
    }


def _server(state: dict) -> MCPServer:
    server = MCPServer("attested-resource-verifier")

    @server.tool()
    def observe_credit(work_item_id: int | None, asset: str, unit: str) -> dict:
        state["calls"] = int(state.get("calls", 0)) + 1
        event_id = str(state.get("event_id", "bank-event-1"))
        claim = {
            "observed": True,
            "external_event_id": event_id,
            "provider_event_id": state.get("provider_event_id", event_id),
            "provider": state.get("provider", "test-bank"),
            "account_binding": state.get("account_binding", "acct-sha256:abc"),
            "settlement_status": state.get("settlement_status", "settled"),
            "asset": asset,
            "unit": unit,
            "kind": "income",
            "amount": state.get("amount", 75.0),
            "settled_at": state.get(
                "settled_at", datetime.now(timezone.utc).isoformat()
            ),
            "work_item_id": work_item_id,
            "evidence": state.get("evidence", "provider-native ledger record"),
        }
        signed = {key: value for key, value in claim.items() if key != "observed"}
        claim["attestation_signature"] = base64.b64encode(
            PROVIDER_SIGNING_KEY.sign(
                json.dumps(
                    signed,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).signature
        ).decode("ascii")
        if state.get("corrupt_signature"):
            claim["attestation_signature"] = base64.b64encode(b"x" * 64).decode("ascii")
        if state.get("omit_signature"):
            claim.pop("attestation_signature", None)
        return claim

    return server


def _accepted_work(state_dir: Path) -> int:
    db = state_dir / "memory.sqlite3"
    workspace = state_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    economy = EconomyStore(db)
    ecology = ResourceEcologyStore(db)
    observations = ObservationStore(db)
    opportunity = economy.create_opportunity(
        title="Settlement target",
        kind="work",
        source_url="https://example.com/task",
        evidence="75 USD target",
        estimated_value=75,
        estimated_cost_value=1,
        unit="VALUE_UNIT",
        probability=0.9,
        estimated_gpu_hours=0.1,
        source="test",
    )
    ecology.upsert_profile(
        opportunity_id=opportunity,
        target_asset="cash",
        target_unit="USD",
        target_amount=75,
        eligibility_confidence=1,
        evidence_quality=1,
        evidence="75 USD target",
    )
    work = ecology.create_work_item(
        opportunity_id=opportunity,
        objective="deliver",
        deliverable_spec="text",
        acceptance_criteria="accepted",
        estimated_gpu_hours=0.1,
        source="test",
    )
    artifact = workspace / "deliverables" / "x.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("x", encoding="utf-8")
    ecology.attach_staged_deliverable(
        opportunity_id=opportunity,
        artifact_path="deliverables/x.txt",
        evidence="staged",
    )
    obs = observations.record(
        source_kind="work_port",
        source_ref="submit_work",
        payload={"ref": "submission"},
        trust=0.8,
        success=True,
        summary="submitted",
    )
    ecology.record_submission(work_item_id=work.id, observation_id=obs.id, evidence="submitted")
    ecology.record_external_outcome(work_item_id=work.id, accepted=True, evidence="accepted")
    return work.id


def test_attested_ingress_requires_final_provider_settlement(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(VERIFY_ENV, VERIFY_KEY)
    monkeypatch.setenv(
        PROVIDER_VERIFY_ENV,
        base64.b64encode(bytes(PROVIDER_SIGNING_KEY.verify_key)).decode("ascii"),
    )
    state_dir = tmp_path / ".elia"
    state = {"settlement_status": "pending"}
    registry = AttestedResourceIngressRegistry(
        state_dir, _config(), mcp_target_overrides={"bank": _server(state)}
    )

    result = registry.execute("check_resource_ingress", {"verifier": "bank_usd"})
    assert result.ok is False
    assert "not finally settled" in str(result.error)
    assert EconomyStore(state_dir / "memory.sqlite3").verified_balance("cash", "USD") == 0


def test_attested_ingress_binds_provider_event_identity(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(VERIFY_ENV, VERIFY_KEY)
    monkeypatch.setenv(
        PROVIDER_VERIFY_ENV,
        base64.b64encode(bytes(PROVIDER_SIGNING_KEY.verify_key)).decode("ascii"),
    )
    state_dir = tmp_path / ".elia"
    state = {"event_id": "evt-1", "provider_event_id": "evt-2"}
    registry = AttestedResourceIngressRegistry(
        state_dir, _config(), mcp_target_overrides={"bank": _server(state)}
    )

    result = registry.execute("check_resource_ingress", {"verifier": "bank_usd"})
    assert result.ok is False
    assert "provider_event_id must equal" in str(result.error)


def test_linked_settlement_must_match_target_amount(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(VERIFY_ENV, VERIFY_KEY)
    monkeypatch.setenv(
        PROVIDER_VERIFY_ENV,
        base64.b64encode(bytes(PROVIDER_SIGNING_KEY.verify_key)).decode("ascii"),
    )
    state_dir = tmp_path / ".elia"
    work_id = _accepted_work(state_dir)
    state = {"amount": 750.0}
    registry = AttestedResourceIngressRegistry(
        state_dir, _config(), mcp_target_overrides={"bank": _server(state)}
    )

    result = registry.execute(
        "check_resource_ingress",
        {"verifier": "bank_usd", "work_item_id": work_id},
    )
    assert result.ok is False
    assert "target amount" in str(result.error)
    assert EconomyStore(state_dir / "memory.sqlite3").verified_balance("cash", "USD") == 0


@pytest.mark.parametrize(
    ("state", "expected_error"),
    [
        ({"provider": "other-bank"}, "provider identity"),
        ({"account_binding": "acct-sha256:other"}, "account binding"),
        ({"omit_signature": True}, "requires attestation_signature"),
        ({"corrupt_signature": True}, "signature verification failed"),
        (
            {"settled_at": "2000-01-01T00:00:00+00:00"},
            "freshness window",
        ),
    ],
)
def test_attested_ingress_rejects_untrusted_or_stale_claim_before_value_ledger(
    monkeypatch,
    tmp_path: Path,
    state: dict,
    expected_error: str,
) -> None:
    monkeypatch.setenv(VERIFY_ENV, VERIFY_KEY)
    monkeypatch.setenv(
        PROVIDER_VERIFY_ENV,
        base64.b64encode(bytes(PROVIDER_SIGNING_KEY.verify_key)).decode("ascii"),
    )
    state_dir = tmp_path / ".elia"
    registry = AttestedResourceIngressRegistry(
        state_dir, _config(), mcp_target_overrides={"bank": _server(state)}
    )

    result = registry.execute("check_resource_ingress", {"verifier": "bank_usd"})
    assert result.ok is False
    assert expected_error in str(result.error)
    assert EconomyStore(state_dir / "memory.sqlite3").verified_balance("cash", "USD") == 0
    with sqlite3.connect(state_dir / "memory.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM resource_ingress_events").fetchone()[0] == 0


def test_attested_ingress_missing_provider_key_is_disabled_before_remote_call(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(VERIFY_ENV, VERIFY_KEY)
    monkeypatch.delenv(PROVIDER_VERIFY_ENV, raising=False)
    state = {"calls": 0}
    state_dir = tmp_path / ".elia"
    registry = AttestedResourceIngressRegistry(
        state_dir, _config(), mcp_target_overrides={"bank": _server(state)}
    )

    assert registry.enabled is False
    assert registry.catalog()["check_resource_ingress"]["readiness"] == (
        "provider_authentication_required"
    )
    result = registry.execute("check_resource_ingress", {"verifier": "bank_usd"})
    assert result.ok is False
    assert state["calls"] == 0
