from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp.server import MCPServer
from nacl.signing import SigningKey

from elia.economy import EconomyStore
from elia.observations import ObservationStore
from elia.resource_ecology import ResourceEcologyStore
from elia.resource_ingress import ResourceIngressRegistry, _canonical
from elia.resource_ingress_hardened import AttestedResourceIngressRegistry


VERIFY_ENV = "ELIA_TEST_RESOURCE_VERIFIER_KEY"
VERIFY_KEY = "0123456789abcdef0123456789abcdef"
PROVIDER_VERIFY_ENV = "ELIA_TEST_RESOURCE_PROVIDER_VERIFY_KEY"
PROVIDER_SIGNING_KEY = SigningKey(bytes(reversed(range(32))))


ARGUMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "work_item_id": {"type": ["integer", "null"]},
        "asset": {"type": "string", "enum": ["cash"]},
        "unit": {"type": "string", "enum": ["USD", "RUB"]},
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
                        "timeout_seconds": 10,
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
                    "expected_provider": "test-bank",
                    "expected_account_binding": "acct-sha256:abc",
                    "provider_verify_key_env": PROVIDER_VERIFY_ENV,
                    "max_attestation_age_seconds": 3600,
                }
            },
        },
    }


def _server(state: dict) -> MCPServer:
    server = MCPServer("resource-verifier")

    @server.tool()
    def observe_credit(work_item_id: int | None, asset: str, unit: str) -> dict:
        state["calls"] = int(state.get("calls", 0)) + 1
        assert asset == "cash"
        assert unit == "USD"
        if not state.get("observed", True):
            return {"observed": False}
        claim = {
            "observed": True,
            "external_event_id": state.get("external_event_id", "bank-event-001"),
            "provider_event_id": state.get("external_event_id", "bank-event-001"),
            "provider": "test-bank",
            "account_binding": "acct-sha256:abc",
            "settlement_status": "settled",
            "asset": asset,
            "unit": unit,
            "kind": "income",
            "amount": state.get("amount", 75.0),
            "settled_at": datetime.now(timezone.utc).isoformat(),
            "work_item_id": work_item_id,
            "evidence": state.get("evidence", "Provider ledger shows settled 75 USD credit."),
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
        return claim

    return server


def _accepted_work(state_dir: Path) -> int:
    database = state_dir / "memory.sqlite3"
    workspace = state_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    economy = EconomyStore(database)
    ecology = ResourceEcologyStore(database)
    observations = ObservationStore(database)
    opportunity_id = economy.create_opportunity(
        title="Accepted external task",
        kind="work",
        source_url="https://example.com/task",
        evidence="Public task promises 75 USD after accepted delivery.",
        estimated_value=75,
        estimated_cost_value=2,
        unit="VALUE_UNIT",
        probability=0.9,
        estimated_gpu_hours=0.2,
        source="test",
    )
    ecology.upsert_profile(
        opportunity_id=opportunity_id,
        target_asset="cash",
        target_unit="USD",
        target_amount=75,
        eligibility_confidence=0.9,
        evidence_quality=0.9,
        evidence="Public task promises 75 USD after accepted delivery.",
    )
    work = ecology.create_work_item(
        opportunity_id=opportunity_id,
        objective="Deliver accepted task",
        deliverable_spec="UTF-8 text artifact",
        acceptance_criteria="External reviewer accepts submission",
        estimated_gpu_hours=0.1,
        source="test",
    )
    artifact = workspace / "deliverables" / "accepted.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("accepted deliverable", encoding="utf-8")
    ecology.attach_staged_deliverable(
        opportunity_id=opportunity_id,
        artifact_path="deliverables/accepted.txt",
        evidence="Local staged artifact.",
    )
    submission_observation = observations.record(
        source_kind="work_port",
        source_ref="submit_work",
        payload={"submission_ref": "submission-001"},
        trust=0.8,
        success=True,
        summary="external submission observed",
    )
    ecology.record_submission(
        work_item_id=work.id,
        observation_id=submission_observation.id,
        evidence="External submission reference observed.",
    )
    ecology.record_external_outcome(
        work_item_id=work.id,
        accepted=True,
        evidence="Independent external work-port outcome says accepted.",
    )
    assert ecology.work_item(work.id).status == "accepted"
    return work.id


def test_independent_verifier_realizes_accepted_work_and_changes_balance_once(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(VERIFY_ENV, VERIFY_KEY)
    state_dir = tmp_path / ".elia"
    work_id = _accepted_work(state_dir)
    remote = {"calls": 0, "external_event_id": "settlement-abc", "amount": 75.0}
    server = _server(remote)
    monkeypatch.setenv(
        PROVIDER_VERIFY_ENV,
        base64.b64encode(bytes(PROVIDER_SIGNING_KEY.verify_key)).decode("ascii"),
    )
    registry = AttestedResourceIngressRegistry(
        state_dir,
        _config(),
        mcp_target_overrides={"bank": server},
    )

    first = registry.execute(
        "check_resource_ingress",
        {"verifier": "bank_usd", "work_item_id": work_id, "amount": 999999},
    )
    assert first.ok is True
    assert first.data["new_resource"] is True
    assert first.data["amount"] == 75.0
    assert first.data["asset"] == "cash"
    assert first.data["unit"] == "USD"
    economy = EconomyStore(state_dir / "memory.sqlite3")
    ecology = ResourceEcologyStore(state_dir / "memory.sqlite3")
    assert economy.verified_balance("cash", "USD") == pytest.approx(75.0)
    realized = ecology.work_item(work_id)
    assert realized.status == "realized"
    assert realized.resource_event_id == first.data["resource_event_id"]

    # Re-observing the same provider event after a later wake must recover the same
    # resource_event/linkage instead of failing or double-counting it.
    second = registry.execute(
        "check_resource_ingress",
        {"verifier": "bank_usd", "work_item_id": work_id},
    )
    assert second.ok is True
    assert second.data["new_resource"] is False
    assert second.data["replayed"] is True
    assert second.data["resource_event_id"] == first.data["resource_event_id"]
    assert economy.verified_balance("cash", "USD") == pytest.approx(75.0)


def test_replayed_external_id_with_changed_claim_fails_closed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(VERIFY_ENV, VERIFY_KEY)
    state_dir = tmp_path / ".elia"
    remote = {"calls": 0, "external_event_id": "same-id", "amount": 25.0, "evidence": "25 USD"}
    server = _server(remote)
    monkeypatch.setenv(
        PROVIDER_VERIFY_ENV,
        base64.b64encode(bytes(PROVIDER_SIGNING_KEY.verify_key)).decode("ascii"),
    )
    registry = AttestedResourceIngressRegistry(
        state_dir,
        _config(),
        mcp_target_overrides={"bank": server},
    )
    first = registry.execute("check_resource_ingress", {"verifier": "bank_usd"})
    assert first.ok is True
    assert EconomyStore(state_dir / "memory.sqlite3").verified_balance("cash", "USD") == 25

    remote["amount"] = 250.0
    remote["evidence"] = "changed claim"
    second = registry.execute("check_resource_ingress", {"verifier": "bank_usd"})
    assert second.ok is False
    assert "conflicts" in str(second.error)
    assert EconomyStore(state_dir / "memory.sqlite3").verified_balance("cash", "USD") == 25


def test_unobserved_verifier_result_does_not_create_resource(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(VERIFY_ENV, VERIFY_KEY)
    state_dir = tmp_path / ".elia"
    remote = {"calls": 0, "observed": False}
    monkeypatch.setenv(
        PROVIDER_VERIFY_ENV,
        base64.b64encode(bytes(PROVIDER_SIGNING_KEY.verify_key)).decode("ascii"),
    )
    registry = AttestedResourceIngressRegistry(
        state_dir,
        _config(),
        mcp_target_overrides={"bank": _server(remote)},
    )
    result = registry.execute("check_resource_ingress", {"verifier": "bank_usd"})
    assert result.ok is True
    assert result.data["new_resource"] is False
    assert EconomyStore(state_dir / "memory.sqlite3").verified_balance("cash", "USD") == 0


def test_verifier_signing_key_cannot_be_delegated_to_mcp_transport(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(VERIFY_ENV, VERIFY_KEY)
    state_dir = tmp_path / ".elia"
    config = _config()
    config["body"]["mcp"]["servers"]["bank"]["headers_from_env"] = {
        "Authorization": VERIFY_ENV
    }
    remote = {"calls": 0}
    monkeypatch.setenv(
        PROVIDER_VERIFY_ENV,
        base64.b64encode(bytes(PROVIDER_SIGNING_KEY.verify_key)).decode("ascii"),
    )
    registry = AttestedResourceIngressRegistry(
        state_dir,
        config,
        mcp_target_overrides={"bank": _server(remote)},
    )
    result = registry.execute("check_resource_ingress", {"verifier": "bank_usd"})
    assert result.ok is False
    assert "must not be delegated" in str(result.error)
    assert remote["calls"] == 0
    assert EconomyStore(state_dir / "memory.sqlite3").verified_balance("cash", "USD") == 0


def test_linked_ingress_requires_exact_accepted_work_resource_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(VERIFY_ENV, VERIFY_KEY)
    state_dir = tmp_path / ".elia"
    work_id = _accepted_work(state_dir)
    config = _config()
    config["resource_ingress"]["verifiers"]["bank_usd"]["unit"] = "RUB"
    monkeypatch.setenv(
        PROVIDER_VERIFY_ENV,
        base64.b64encode(bytes(PROVIDER_SIGNING_KEY.verify_key)).decode("ascii"),
    )
    registry = AttestedResourceIngressRegistry(
        state_dir,
        config,
        mcp_target_overrides={"bank": _server({"calls": 0})},
    )
    result = registry.execute(
        "check_resource_ingress",
        {"verifier": "bank_usd", "work_item_id": work_id},
    )
    assert result.ok is False
    assert "does not match accepted work resource profile" in str(result.error)
    assert EconomyStore(state_dir / "memory.sqlite3").verified_balance("cash", "RUB") == 0


def test_resource_ingress_canonical_json_is_order_stable_and_strict() -> None:
    assert _canonical({"a": 1, "nested": {"x": 2, "y": 3}}) == _canonical(
        {"nested": {"y": 3, "x": 2}, "a": 1}
    )

    class Stringifiable:
        def __str__(self) -> str:
            return "ambiguous"

    for invalid in ({"amount": float("nan")}, {"value": Stringifiable()}):
        with pytest.raises(ValueError):
            _canonical(invalid)


def test_legacy_resource_ingress_never_claims_provider_authentication(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(VERIFY_ENV, VERIFY_KEY)
    monkeypatch.setenv(
        PROVIDER_VERIFY_ENV,
        base64.b64encode(bytes(PROVIDER_SIGNING_KEY.verify_key)).decode("ascii"),
    )
    remote = {"calls": 0}
    registry = ResourceIngressRegistry(
        tmp_path / ".elia",
        _config(),
        mcp_target_overrides={"bank": _server(remote)},
    )
    assert registry.enabled is False
    assert registry.catalog()["check_resource_ingress"]["readiness"] == (
        "provider_authentication_required"
    )
    result = registry.execute("check_resource_ingress", {"verifier": "bank_usd"})
    assert result.ok is False
    assert remote["calls"] == 0
