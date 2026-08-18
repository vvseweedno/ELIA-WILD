from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp.server import MCPServer

from elia.brain import Decision
from elia.config import load_config
from elia.economy import EconomyStore
from elia.external_work_runtime import ExternalWorkOrganismRuntime
from elia.resource_ecology import ResourceEcologyStore
from elia.work_ports import WorkPortRegistry


def _config(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ELIA_STATE_DIR", str(tmp_path / ".elia"))
    config = load_config(Path(__file__).resolve().parents[1] / "config" / "genesis.yaml")
    config.raw_tools.setdefault("body", {})["mcp"] = {
        "enabled": True,
        "servers": {
            "market": {
                "enabled": True,
                "allow_tool_calls": True,
                "allowed_tools": ["submit_candidate", "candidate_status"],
                "allowed_resources": [],
                "timeout_seconds": 10,
            }
        },
    }
    config.raw_tools["work_ports"] = {
        "enabled": True,
        "ports": {
            "marketplace": {
                "enabled": True,
                "server": "market",
                "submit_tool": "submit_candidate",
                "outcome_tool": "candidate_status",
                "max_artifact_bytes": 100_000,
            }
        },
    }
    return config


def _server():
    server = MCPServer("test-work-port")
    state = {"status": "pending"}

    @server.tool()
    def submit_candidate(
        work_item_id: int,
        opportunity_id: int,
        objective: str,
        deliverable: dict,
        acceptance_criteria: str,
    ) -> dict:
        assert work_item_id > 0
        assert opportunity_id > 0
        assert objective
        assert acceptance_criteria
        assert deliverable["text"] == "ready deliverable"
        assert len(deliverable["sha256"]) == 64
        return {"submission_ref": f"submission-{work_item_id}", "status": "submitted"}

    @server.tool()
    def candidate_status(work_item_id: int, submission_ref: str) -> dict:
        assert submission_ref == f"submission-{work_item_id}"
        if state["status"] == "pending":
            return {"status": "pending", "evidence": ""}
        if state["status"] == "accepted":
            return {
                "status": "accepted",
                "evidence": "External reviewer accepted the submitted work.",
            }
        return {
            "status": "rejected",
            "evidence": "External reviewer rejected the submitted work.",
        }

    return server, state


def _staged_work(config) -> tuple[int, int]:
    database = config.runtime.state_dir / "memory.sqlite3"
    workspace = config.runtime.state_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    economy = EconomyStore(database)
    ecology = ResourceEcologyStore(database)
    opportunity_id = economy.create_opportunity(
        title="External paid task",
        kind="work",
        source_url="https://example.com/task",
        evidence="Public task states 75 USD after accepted delivery.",
        estimated_value=75,
        estimated_cost_value=3,
        unit="VALUE_UNIT",
        probability=0.75,
        estimated_gpu_hours=0.2,
        source="test",
    )
    ecology.upsert_profile(
        opportunity_id=opportunity_id,
        target_asset="cash",
        target_unit="USD",
        target_amount=75,
        eligibility_confidence=0.8,
        evidence_quality=0.8,
        evidence="Public task states 75 USD after accepted delivery.",
    )
    work = ecology.create_work_item(
        opportunity_id=opportunity_id,
        objective="Submit the requested response.",
        deliverable_spec="One UTF-8 text artifact.",
        acceptance_criteria="External reviewer accepts the response.",
        estimated_gpu_hours=0.1,
        source="test",
    )
    artifact = workspace / "deliverables" / "candidate.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("ready deliverable", encoding="utf-8")
    ecology.attach_staged_deliverable(
        opportunity_id=opportunity_id,
        artifact_path="deliverables/candidate.txt",
        evidence="Artifact created locally.",
    )
    return opportunity_id, work.id


def test_real_mcp_work_port_submission_and_outcome_do_not_mint_payment(
    monkeypatch, tmp_path: Path
) -> None:
    config = _config(monkeypatch, tmp_path)
    server, remote = _server()
    _, work_id = _staged_work(config)
    registry = WorkPortRegistry(
        config.runtime.state_dir / "workspace",
        config.raw_tools,
        mcp_target_overrides={"market": server},
    )
    assert registry.enabled is True
    assert registry.catalog()["submit_work"]["enabled"] is True

    submitted = registry.execute(
        "submit_work",
        {
            "port": "marketplace",
            "work_item_id": work_id,
            # These must never select transport authority; the fixed port binding wins.
            "server": "attacker-controlled",
            "tool": "arbitrary_tool",
        },
    )
    assert submitted.ok is True
    submission = registry.store.submission_for_work(work_id)
    assert submission is not None
    assert submission.port_name == "marketplace"
    assert submission.submission_ref == f"submission-{work_id}"
    ecology = ResourceEcologyStore(config.runtime.state_dir / "memory.sqlite3")
    assert ecology.work_item(work_id).status == "submitted"
    assert EconomyStore(config.runtime.state_dir / "memory.sqlite3").verified_balance("cash", "USD") == 0

    pending = registry.execute("check_work_outcome", {"work_item_id": work_id})
    assert pending.ok is True
    assert pending.data["remote_status"] == "pending"
    assert ecology.work_item(work_id).status == "submitted"

    remote["status"] = "accepted"
    accepted = registry.execute("check_work_outcome", {"work_item_id": work_id})
    assert accepted.ok is True
    assert accepted.data["remote_status"] == "accepted"
    assert ecology.work_item(work_id).status == "accepted"
    # Acceptance is explicitly not payment.
    assert EconomyStore(config.runtime.state_dir / "memory.sqlite3").verified_balance("cash", "USD") == 0


def test_work_port_rejects_unknown_port_without_external_call(monkeypatch, tmp_path: Path) -> None:
    config = _config(monkeypatch, tmp_path)
    server, _ = _server()
    _, work_id = _staged_work(config)
    registry = WorkPortRegistry(
        config.runtime.state_dir / "workspace",
        config.raw_tools,
        mcp_target_overrides={"market": server},
    )
    result = registry.execute("submit_work", {"port": "unknown", "work_item_id": work_id})
    assert result.ok is False
    assert "unknown or disabled work port" in str(result.error)
    assert ResourceEcologyStore(config.runtime.state_dir / "memory.sqlite3").work_item(work_id).status == "staged"


class SubmitBrain:
    def __init__(self, work_id: int):
        self.work_id = work_id

    def decide(self, context: dict) -> Decision:
        assert context["capabilities"]["catalog"]["submit_work"]["enabled"] is True
        assert context["work_ports"]["enabled"] is True
        return Decision(
            objective="Submit the already staged authorized work item.",
            summary="Use the configured marketplace work port; do not claim acceptance or payment.",
            action_name="submit_work",
            action_args={"port": "marketplace", "work_item_id": self.work_id},
            prediction={
                "action_success_probability": 0.9,
                "expected_outcome": "A submission reference is observed and work becomes submitted.",
                "expected_information_gain": 0.2,
                "expected_value": 0,
                "unit": "VALUE_UNIT",
            },
            sleep_seconds=0,
        )


def test_external_work_runtime_declares_and_executes_port_as_real_capability(
    monkeypatch, tmp_path: Path
) -> None:
    config = _config(monkeypatch, tmp_path)
    server, _ = _server()
    _, work_id = _staged_work(config)
    runtime = ExternalWorkOrganismRuntime(
        config,
        brain=SubmitBrain(work_id),
        mcp_target_overrides={"market": server},
    )
    report = runtime.cycle()
    assert report["result"]["ok"] is True
    assert report["decision"]["action_name"] == "submit_work"
    assert report["work_ports"]["enabled"] is True
    assert runtime.resource_ecology_store.work_item(work_id).status == "submitted"
    assert runtime.economy.verified_balance("cash", "USD") == 0
