from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp.server import MCPServer

from elia.economy import EconomyStore
from elia.observations import ObservationStore
from elia.resource_ecology import ResourceEcologyStore
from elia.resource_ingress_hardened import AttestedResourceIngressRegistry


VERIFY_ENV = "ELIA_TEST_ATTESTED_RESOURCE_KEY"
VERIFY_KEY = "0123456789abcdef0123456789abcdef"


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
                }
            },
        },
    }


def _server(state: dict) -> MCPServer:
    server = MCPServer("attested-resource-verifier")

    @server.tool()
    def observe_credit(work_item_id: int | None, asset: str, unit: str) -> dict:
        event_id = str(state.get("event_id", "bank-event-1"))
        return {
            "observed": True,
            "external_event_id": event_id,
            "provider_event_id": state.get("provider_event_id", event_id),
            "provider": state.get("provider", "test-bank"),
            "account_binding": state.get("account_binding", "acct-sha256:abc"),
            "settlement_status": state.get("settlement_status", "settled"),
            "amount": state.get("amount", 75.0),
            "evidence": state.get("evidence", "provider-native ledger record"),
        }

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
