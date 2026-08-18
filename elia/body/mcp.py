from __future__ import annotations

from contextlib import asynccontextmanager
import fnmatch
import importlib.util
import os
from typing import Any, AsyncIterator

from .asyncio_bridge import run_sync
from .net import assert_http_url
from .types import BodyCapability, BodyResult


class MCPBody:
    """MCP v2 client organ restricted to explicitly configured servers/resources/tools."""

    MAX_PAGES = 32

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        target_overrides: dict[str, Any] | None = None,
    ):
        self.config = dict(config or {})
        self.target_overrides = dict(target_overrides or {})

    @property
    def installed(self) -> bool:
        return importlib.util.find_spec("mcp") is not None

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False)) and self.installed and bool(self.servers())

    def servers(self) -> dict[str, dict[str, Any]]:
        raw = self.config.get("servers") or {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(name)[:64]: dict(item)
            for name, item in raw.items()
            if isinstance(item, dict) and bool(item.get("enabled", True))
        }

    def capabilities(self) -> list[BodyCapability]:
        readiness = "ready" if self.installed else "mcp_v2_not_installed"
        base_enabled = self.enabled
        return [
            BodyCapability(
                "mcp_discover",
                "Negotiate with one configured MCP server and list its tools/resources/templates.",
                "{server: configured_name}",
                "configured_mcp_discovery",
                "opens a configured MCP connection; no tool is executed",
                "configured_mcp_server",
                "network_or_local_process",
                base_enabled,
                readiness if self.config.get("enabled", False) else "disabled",
            ),
            BodyCapability(
                "mcp_call",
                "Call one explicitly allow-listed tool on one configured MCP server.",
                "{server: configured_name, tool: str, arguments?: object}",
                "configured_mcp_tool_call",
                "side effects are defined by the selected allow-listed remote tool",
                "configured_mcp_server",
                "network_or_local_process",
                base_enabled and any(bool(item.get("allow_tool_calls", False)) for item in self.servers().values()),
                readiness,
            ),
            BodyCapability(
                "mcp_read_resource",
                "Read one allow-listed resource URI from one configured MCP server.",
                "{server: configured_name, uri: str}",
                "configured_mcp_resource_read",
                "read-only protocol request",
                "configured_mcp_server",
                "network_or_local_process",
                base_enabled,
                readiness,
            ),
        ]

    def _server(self, name: str) -> dict[str, Any]:
        item = self.servers().get(str(name))
        if item is None:
            raise ValueError(f"unknown or disabled MCP server: {name!r}")
        return item

    @staticmethod
    def _allowed(value: str, patterns: list[Any]) -> bool:
        return any(fnmatch.fnmatch(value, str(pattern)) for pattern in patterns)

    def _headers(self, server: dict[str, Any]) -> dict[str, str]:
        mapping = server.get("headers_from_env") or {}
        if not isinstance(mapping, dict):
            raise ValueError("headers_from_env must be an object")
        headers: dict[str, str] = {}
        for header, env_name in mapping.items():
            env_name = str(env_name).strip()
            if not env_name:
                continue
            value = os.getenv(env_name)
            if value is None:
                raise RuntimeError(f"required MCP credential environment variable is missing: {env_name}")
            headers[str(header)] = value
        return headers

    @asynccontextmanager
    async def _client(self, name: str) -> AsyncIterator[Any]:
        if not self.enabled:
            raise RuntimeError("MCP body is disabled or mcp v2 is unavailable")
        from mcp import Client

        server = self._server(name)
        timeout = max(1.0, min(float(server.get("timeout_seconds", 30.0)), 300.0))
        override = self.target_overrides.get(name)
        if override is not None:
            async with Client(override, read_timeout_seconds=timeout, raise_exceptions=True) as client:
                yield client
            return

        url = str(server.get("url", "")).strip()
        if not url:
            raise ValueError("configured MCP server has no URL")
        assert_http_url(url, allow_private=bool(server.get("allow_private", False)))
        headers = self._headers(server)
        if not headers:
            async with Client(url, read_timeout_seconds=timeout) as client:
                yield client
            return

        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        async with httpx2.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=httpx2.Timeout(timeout, read=max(timeout, 60.0)),
        ) as http_client:
            transport = streamable_http_client(url, http_client=http_client)
            async with Client(transport, read_timeout_seconds=timeout) as client:
                yield client

    async def _discover_async(self, name: str) -> dict[str, Any]:
        tools: list[dict[str, Any]] = []
        resources: list[dict[str, Any]] = []
        templates: list[dict[str, Any]] = []
        async with self._client(name) as client:
            cursor = None
            for _ in range(self.MAX_PAGES):
                result = await client.list_tools(cursor=cursor)
                tools.extend(
                    {
                        "name": tool.name,
                        "title": getattr(tool, "title", None),
                        "description": getattr(tool, "description", None),
                        "input_schema": getattr(tool, "input_schema", None),
                    }
                    for tool in result.tools
                )
                cursor = result.next_cursor
                if cursor is None:
                    break
            cursor = None
            for _ in range(self.MAX_PAGES):
                result = await client.list_resources(cursor=cursor)
                resources.extend(
                    {
                        "uri": str(resource.uri),
                        "name": getattr(resource, "name", None),
                        "description": getattr(resource, "description", None),
                        "mime_type": getattr(resource, "mime_type", None),
                    }
                    for resource in result.resources
                )
                cursor = result.next_cursor
                if cursor is None:
                    break
            cursor = None
            for _ in range(self.MAX_PAGES):
                result = await client.list_resource_templates(cursor=cursor)
                templates.extend(
                    {
                        "uri_template": str(template.uri_template),
                        "name": getattr(template, "name", None),
                        "description": getattr(template, "description", None),
                    }
                    for template in result.resource_templates
                )
                cursor = result.next_cursor
                if cursor is None:
                    break
            info = client.server_info
            return {
                "server": name,
                "protocol_version": client.protocol_version,
                "server_info": {
                    "name": getattr(info, "name", None),
                    "version": getattr(info, "version", None),
                }
                if info is not None
                else None,
                "tools": tools[:512],
                "resources": resources[:512],
                "resource_templates": templates[:512],
            }

    def discover(self, name: str) -> BodyResult:
        try:
            return BodyResult(True, "mcp_discover", run_sync(self._discover_async(name)))
        except Exception as exc:
            return BodyResult(False, "mcp_discover", error=f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _content_blocks(result: Any) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for block in getattr(result, "content", []) or []:
            item: dict[str, Any] = {"type": type(block).__name__}
            for attr in ("text", "mime_type", "uri", "name"):
                if hasattr(block, attr):
                    item[attr] = str(getattr(block, attr))[:100_000]
            if hasattr(block, "data"):
                data = getattr(block, "data")
                item["data_size"] = len(data) if hasattr(data, "__len__") else None
            blocks.append(item)
        return blocks

    async def _call_async(self, server_name: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        server = self._server(server_name)
        if not bool(server.get("allow_tool_calls", False)):
            raise PermissionError("MCP tool calls are disabled for this server")
        patterns = list(server.get("allowed_tools") or [])
        if not patterns or not self._allowed(tool, patterns):
            raise PermissionError(f"MCP tool is not allow-listed: {tool}")
        async with self._client(server_name) as client:
            result = await client.call_tool(tool, arguments)
            return {
                "server": server_name,
                "tool": tool,
                "is_error": bool(result.is_error),
                "structured_content": result.structured_content,
                "content": self._content_blocks(result),
            }

    def call(self, server: str, tool: str, arguments: dict[str, Any] | None = None) -> BodyResult:
        try:
            data = run_sync(self._call_async(server, str(tool), dict(arguments or {})))
            return BodyResult(not data["is_error"], "mcp_call", data, error="MCP tool returned is_error=true" if data["is_error"] else None)
        except Exception as exc:
            return BodyResult(False, "mcp_call", error=f"{type(exc).__name__}: {exc}")

    async def _read_resource_async(self, server_name: str, uri: str) -> dict[str, Any]:
        server = self._server(server_name)
        patterns = list(server.get("allowed_resources") or [])
        if not patterns or not self._allowed(uri, patterns):
            raise PermissionError(f"MCP resource is not allow-listed: {uri}")
        async with self._client(server_name) as client:
            result = await client.read_resource(uri)
            contents: list[dict[str, Any]] = []
            for block in result.contents:
                item: dict[str, Any] = {"type": type(block).__name__}
                for attr in ("uri", "mime_type", "text"):
                    if hasattr(block, attr):
                        item[attr] = str(getattr(block, attr))[:200_000]
                if hasattr(block, "blob"):
                    blob = getattr(block, "blob")
                    item["blob_size"] = len(blob) if hasattr(blob, "__len__") else None
                contents.append(item)
            return {"server": server_name, "uri": uri, "contents": contents}

    def read_resource(self, server: str, uri: str) -> BodyResult:
        try:
            return BodyResult(True, "mcp_read_resource", run_sync(self._read_resource_async(server, str(uri))))
        except Exception as exc:
            return BodyResult(False, "mcp_read_resource", error=f"{type(exc).__name__}: {exc}")
