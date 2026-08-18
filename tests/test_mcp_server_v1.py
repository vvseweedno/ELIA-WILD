from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import Client

from elia.mcp_server import build_mcp_server


def _config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "genesis.yaml"


def _structured(result) -> dict:
    payload = result.structured_content
    if isinstance(payload, dict):
        if set(payload) == {"result"} and isinstance(payload["result"], dict):
            return payload["result"]
        return payload
    for block in result.content or []:
        text = getattr(block, "text", None)
        if text:
            item = json.loads(text)
            if isinstance(item, dict):
                return item
    raise AssertionError("MCP result contained no structured dictionary")


def test_real_inprocess_mcp_server_exposes_sanitized_organism_port(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ELIA_STATE_DIR", str(tmp_path / ".elia"))
    server = build_mcp_server(_config_path())

    async def exercise() -> None:
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {
                "elia_status",
                "elia_preflight",
                "elia_executive",
                "elia_resource_ecology",
                "elia_world_query",
                "elia_sensorium_recent",
                "elia_body_diagnostics",
                "elia_homeostasis",
            }.issubset(names)

            status_result = await client.call_tool("elia_status", {})
            assert status_result.is_error is False
            status = _structured(status_result)
            assert status["identity"]["identity_id"] == "elia-wild"
            assert "metabolism" in status
            assert "compute_energy" in status["metabolism"]
            assert "resource_ecology" in status
            assert "exact_bottleneck_candidate_count" in status["resource_ecology"]
            assert "homeostasis" in status
            assert status["homeostasis"]["metabolism"] == status["metabolism"]
            assert "executive" in status
            assert status["executive"]["enabled"] is True
            assert "plan" in status["executive"]
            assert "energy" in status["executive"]
            assert "digital_body" in status
            assert "sensorium" in status

            executive_result = await client.call_tool("elia_executive", {})
            assert executive_result.is_error is False
            executive = _structured(executive_result)
            assert executive["enabled"] is True
            assert isinstance(executive["plan"], dict)
            assert executive["plan"]["cognitive_budget"]["tier"] in {
                "none",
                "low",
                "normal",
                "deep",
            }
            assert "energy" in executive
            assert "recent" in executive

            ecology_result = await client.call_tool("elia_resource_ecology", {})
            assert ecology_result.is_error is False
            ecology = _structured(ecology_result)
            assert "candidates" in ecology
            assert "active_work" in ecology
            assert "epistemic_rule" in ecology
            serialized_ecology = json.dumps(ecology, sort_keys=True)
            assert "external_evidence" not in serialized_ecology

            world_result = await client.call_tool(
                "elia_world_query",
                {"text": "anything", "limit": 4},
            )
            assert world_result.is_error is False
            world = _structured(world_result)
            assert "beliefs" in world
            assert "verified facts" in world["epistemic_rule"]

            sensor_result = await client.call_tool("elia_sensorium_recent", {"limit": 4})
            sensor = _structured(sensor_result)
            assert "observations" in sensor
            for item in sensor["observations"]:
                assert "payload" not in item
                assert "payload_sha256" in item

            homeostasis_result = await client.call_tool("elia_homeostasis", {})
            homeostasis = _structured(homeostasis_result)
            assert "metabolism" in homeostasis
            assert "compute_energy" in homeostasis["metabolism"]

            resource = await client.read_resource("elia://identity")
            assert resource.contents
            text = getattr(resource.contents[0], "text", "")
            identity = json.loads(text)
            assert identity["identity_id"] == "elia-wild"
            assert identity["body_version"].startswith("1.4.")

            ecology_resource = await client.read_resource("elia://resource-ecology")
            assert ecology_resource.contents
            ecology_text = getattr(ecology_resource.contents[0], "text", "")
            ecology_from_resource = json.loads(ecology_text)
            assert "candidates" in ecology_from_resource

    asyncio.run(exercise())


def test_mcp_server_http_transport_policy_is_loopback_only() -> None:
    from elia.mcp_server import _is_loopback_host

    assert _is_loopback_host("127.0.0.1") is True
    assert _is_loopback_host("::1") is True
    assert _is_loopback_host("localhost") is True
    assert _is_loopback_host("0.0.0.0") is False
    assert _is_loopback_host("192.168.1.10") is False
    assert _is_loopback_host("example.com") is False
