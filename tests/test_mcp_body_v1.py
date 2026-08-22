from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from mcp.server import MCPServer

from elia.body.mcp import MCPBody


ADD_ARGUMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "a": {"type": "integer"},
        "b": {"type": "integer"},
    },
    "required": ["a", "b"],
    "additionalProperties": False,
}


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
                    "tool_argument_schemas": {"add": ADD_ARGUMENT_SCHEMA},
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

    out_of_scope = body.call("local", "add", {"a": 1, "b": 2, "command": "extra"})
    assert out_of_scope.ok is False
    assert "out-of-scope fields" in (out_of_scope.error or "")

    resource = body.read_resource("local", "test://hello")
    assert resource.ok is True, resource.error
    assert any(
        "hello from MCP" in item.get("text", "")
        for item in resource.data["contents"]
    )


def test_mcp_server_name_cannot_be_invented_by_model() -> None:
    body = _body(_server())
    result = body.discover("unconfigured")
    assert result.ok is False
    assert "unknown or disabled MCP server" in (result.error or "")


def test_url_mcp_transport_requires_network_isolation_even_without_credentials() -> None:
    body = MCPBody(
        {
            "enabled": True,
            "servers": {
                "public": {
                    "enabled": True,
                    "url": "https://example.com/mcp",
                    "allow_tool_calls": False,
                    "allowed_resources": [],
                }
            },
        }
    )
    assert body.enabled is False
    caps = {item.name: item for item in body.capabilities()}
    assert caps["mcp_discover"].enabled is False
    assert caps["mcp_discover"].readiness == "network_isolation_or_transport_required"
    result = body.discover("public")
    assert result.ok is False
    assert "network_isolation_required" in (result.error or "")
