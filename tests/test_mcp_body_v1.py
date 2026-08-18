from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from mcp.server import MCPServer

from elia.body.mcp import MCPBody


def _server() -> MCPServer:
    server = MCPServer("ELIA Body Test")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    @server.tool()
    def forbidden_operation() -> str:
        return "should never be callable through the configured body"

    @server.resource("test://hello")
    def hello() -> str:
        return "hello from MCP"

    return server


def _body(server: MCPServer) -> MCPBody:
    return MCPBody(
        {
            "enabled": True,
            "servers": {
                "local": {
                    "enabled": True,
                    "allow_tool_calls": True,
                    "allowed_tools": ["add"],
                    "allowed_resources": ["test://*"],
                    "timeout_seconds": 5,
                }
            },
        },
        target_overrides={"local": server},
    )


def test_mcp_v2_inprocess_discovery_call_and_resource() -> None:
    body = _body(_server())
    discovered = body.discover("local")
    assert discovered.ok is True, discovered.error
    names = {item["name"] for item in discovered.data["tools"]}
    assert {"add", "forbidden_operation"}.issubset(names)
    assert discovered.data["protocol_version"]

    called = body.call("local", "add", {"a": 20, "b": 22})
    assert called.ok is True, called.error
    assert called.data["is_error"] is False
    assert called.data["structured_content"] == {"result": 42}

    denied = body.call("local", "forbidden_operation", {})
    assert denied.ok is False
    assert "not allow-listed" in (denied.error or "")

    resource = body.read_resource("local", "test://hello")
    assert resource.ok is True, resource.error
    assert any("hello from MCP" in item.get("text", "") for item in resource.data["contents"])


def test_mcp_server_name_cannot_be_invented_by_model() -> None:
    body = _body(_server())
    result = body.discover("unconfigured")
    assert result.ok is False
    assert "unknown or disabled MCP server" in (result.error or "")
