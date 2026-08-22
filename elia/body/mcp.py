from __future__ import annotations

from contextlib import asynccontextmanager
import fnmatch
import importlib.util
import json
import math
import os
from typing import Any, AsyncIterator, Callable

from .asyncio_bridge import run_sync
from .net import assert_http_url, network_isolation_attested
from .types import (
    BodyCapability,
    BodyInputError,
    BodyResult,
    bounded_json_value,
    validate_json_schema,
)


class MCPBody:
    """MCP v2 client restricted to configured servers/resources/tools.

    The official Streamable HTTP client resolves hostnames inside its own transport, so
    application prevalidation cannot provide the same DNS pinning guarantee as ELIA's
    raw HTTP body. URL-based MCP servers therefore require deployment network isolation
    confirmation *even without credentials*. In-process target overrides are test/local
    transports and do not cross that network boundary.
    """

    MAX_PAGES = 32
    MAX_DISCOVERY_ITEMS = 512
    MAX_CONTENT_BLOCKS = 64
    MAX_PROJECTED_BYTES = 512_000

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        target_overrides: dict[str, Any] | None = None,
        network_isolation_verifier: Callable[[dict[str, Any]], bool] | None = None,
    ):
        self.config = dict(config or {})
        self.target_overrides = dict(target_overrides or {})
        self._network_isolation_verifier = network_isolation_verifier

    @property
    def installed(self) -> bool:
        return importlib.util.find_spec("mcp") is not None

    def servers(self) -> dict[str, dict[str, Any]]:
        raw = self.config.get("servers") or {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(name)[:64]: dict(item)
            for name, item in raw.items()
            if isinstance(item, dict) and bool(item.get("enabled", True))
        }

    def _server_readiness(self, name: str, server: dict[str, Any]) -> str:
        if not self.installed:
            return "mcp_v2_not_installed"
        if name in self.target_overrides:
            return "ready_inprocess"
        if not str(server.get("url", "")).strip():
            return "url_required"
        if not network_isolation_attested(
            server,
            verifier=self._network_isolation_verifier,
        ):
            return "network_isolation_required"
        return "ready"

    def _ready_server_names(self) -> set[str]:
        return {
            name
            for name, server in self.servers().items()
            if self._server_readiness(name, server) in {"ready", "ready_inprocess"}
        }

    @property
    def enabled(self) -> bool:
        return (
            bool(self.config.get("enabled", False))
            and self.installed
            and bool(self._ready_server_names())
        )

    def capabilities(self) -> list[BodyCapability]:
        configured = self.servers()
        ready_names = self._ready_server_names() if self.installed else set()
        if not bool(self.config.get("enabled", False)):
            readiness = "disabled"
        elif not self.installed:
            readiness = "mcp_v2_not_installed"
        elif not configured:
            readiness = "no_configured_servers"
        elif not ready_names:
            readiness = "network_isolation_or_transport_required"
        else:
            readiness = "ready"
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
                readiness,
            ),
            BodyCapability(
                "mcp_call",
                "Call one explicitly allow-listed tool on one configured MCP server.",
                "{server: configured_name, tool: str, arguments?: object}",
                "configured_mcp_tool_call",
                "side effects are defined by the selected allow-listed remote tool",
                "configured_mcp_server",
                "network_or_local_process",
                base_enabled
                and any(
                    name in ready_names
                    and bool(item.get("allow_tool_calls", False))
                    and bool(item.get("tool_argument_schemas"))
                    for name, item in configured.items()
                ),
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
        name = str(name)
        item = self.servers().get(name)
        if item is None:
            raise ValueError(f"unknown or disabled MCP server: {name!r}")
        readiness = self._server_readiness(name, item)
        if readiness not in {"ready", "ready_inprocess"}:
            raise RuntimeError(f"MCP server {name!r} unavailable: {readiness}")
        return item

    @staticmethod
    def _allowed(value: str, patterns: list[Any]) -> bool:
        return any(fnmatch.fnmatch(value, str(pattern)) for pattern in patterns)

    @staticmethod
    def _tool_arguments(server: dict[str, Any], tool: str, arguments: Any) -> dict[str, Any]:
        schemas = server.get("tool_argument_schemas") or {}
        if not isinstance(schemas, dict):
            raise PermissionError("MCP tool_argument_schemas must be an object")
        schema = schemas.get(tool)
        validated = validate_json_schema(
            arguments,
            schema,
            field=f"MCP arguments for {tool}",
        )
        if not isinstance(validated, dict):
            raise BodyInputError("MCP tool arguments must validate as an object")
        return validated

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
                raise RuntimeError(
                    f"required MCP credential environment variable is missing: {env_name}"
                )
            headers[str(header)] = value
        return headers

    @asynccontextmanager
    async def _client(self, name: str) -> AsyncIterator[Any]:
        if not bool(self.config.get("enabled", False)) or not self.installed:
            raise RuntimeError("MCP body is disabled or mcp v2 is unavailable")
        from mcp import Client

        server = self._server(name)
        timeout = self._operation_timeout(name)
        override = self.target_overrides.get(name)
        if override is not None:
            async with Client(
                override,
                read_timeout_seconds=timeout,
                raise_exceptions=True,
            ) as client:
                yield client
            return

        # `_server` has already enforced network isolation for URL transports. URL
        # validation still rejects unsupported schemes/embedded credentials/private
        # resolution at this instant, but the deployment sandbox is the anti-rebinding
        # boundary for the transport's later DNS resolution.
        url = str(server.get("url", "")).strip()
        allow_private = bool(server.get("allow_private", False))
        assert_http_url(url, allow_private=allow_private)
        headers = self._headers(server)

        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        async with httpx2.AsyncClient(
            headers=headers,
            follow_redirects=False,
            timeout=httpx2.Timeout(timeout, read=max(timeout, 60.0)),
        ) as http_client:
            transport = streamable_http_client(url, http_client=http_client)
            async with Client(transport, read_timeout_seconds=timeout) as client:
                yield client

    def _operation_timeout(self, name: str) -> float:
        server = self._server(name)
        raw = server.get("operation_timeout_seconds", server.get("timeout_seconds", 30.0))
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("MCP operation timeout must be a finite positive number")
        timeout = float(raw)
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 300.0:
            raise ValueError("MCP operation timeout must be finite, positive and at most 300 seconds")
        return timeout

    async def _discover_async(self, name: str) -> dict[str, Any]:
        tools: list[dict[str, Any]] = []
        resources: list[dict[str, Any]] = []
        templates: list[dict[str, Any]] = []
        async with self._client(name) as client:
            cursor = None
            seen_cursors: set[str] = set()
            for _ in range(self.MAX_PAGES):
                result = await client.list_tools(cursor=cursor)
                page = list(result.tools or [])
                if len(page) > self.MAX_DISCOVERY_ITEMS:
                    raise ValueError("MCP tool discovery page exceeds item limit")
                for tool in page[: max(0, self.MAX_DISCOVERY_ITEMS - len(tools))]:
                    tools.append({
                        "name": tool.name,
                        "title": str(getattr(tool, "title", "") or "")[:1000],
                        "description": str(getattr(tool, "description", "") or "")[:4000],
                        "input_schema": bounded_json_value(
                            getattr(tool, "input_schema", None),
                            field="MCP discovered input schema",
                            max_bytes=64_000,
                            max_items=256,
                        ),
                    })
                cursor = result.next_cursor
                if cursor is None:
                    break
                cursor_key = str(cursor)
                if cursor_key in seen_cursors:
                    raise ValueError("MCP tool discovery repeated a cursor")
                seen_cursors.add(cursor_key)
                if len(tools) >= self.MAX_DISCOVERY_ITEMS:
                    break
            cursor = None
            seen_cursors = set()
            for _ in range(self.MAX_PAGES):
                result = await client.list_resources(cursor=cursor)
                page = list(result.resources or [])
                if len(page) > self.MAX_DISCOVERY_ITEMS:
                    raise ValueError("MCP resource discovery page exceeds item limit")
                for resource in page[: max(0, self.MAX_DISCOVERY_ITEMS - len(resources))]:
                    resources.append({
                        "uri": str(resource.uri),
                        "name": str(getattr(resource, "name", "") or "")[:1000],
                        "description": str(getattr(resource, "description", "") or "")[:4000],
                        "mime_type": str(getattr(resource, "mime_type", "") or "")[:256],
                    })
                cursor = result.next_cursor
                if cursor is None:
                    break
                cursor_key = str(cursor)
                if cursor_key in seen_cursors:
                    raise ValueError("MCP resource discovery repeated a cursor")
                seen_cursors.add(cursor_key)
                if len(resources) >= self.MAX_DISCOVERY_ITEMS:
                    break
            cursor = None
            seen_cursors = set()
            for _ in range(self.MAX_PAGES):
                result = await client.list_resource_templates(cursor=cursor)
                page = list(result.resource_templates or [])
                if len(page) > self.MAX_DISCOVERY_ITEMS:
                    raise ValueError("MCP template discovery page exceeds item limit")
                for template in page[: max(0, self.MAX_DISCOVERY_ITEMS - len(templates))]:
                    templates.append({
                        "uri_template": str(template.uri_template),
                        "name": str(getattr(template, "name", "") or "")[:1000],
                        "description": str(getattr(template, "description", "") or "")[:4000],
                    })
                cursor = result.next_cursor
                if cursor is None:
                    break
                cursor_key = str(cursor)
                if cursor_key in seen_cursors:
                    raise ValueError("MCP template discovery repeated a cursor")
                seen_cursors.add(cursor_key)
                if len(templates) >= self.MAX_DISCOVERY_ITEMS:
                    break
            info = client.server_info
            return bounded_json_value({
                "server": name,
                "protocol_version": client.protocol_version,
                "server_info": (
                    {
                        "name": getattr(info, "name", None),
                        "version": getattr(info, "version", None),
                    }
                    if info is not None
                    else None
                ),
                "tools": tools,
                "resources": resources,
                "resource_templates": templates,
            }, field="MCP discovery result", max_bytes=self.MAX_PROJECTED_BYTES)

    def discover(self, name: str) -> BodyResult:
        try:
            timeout = self._operation_timeout(name)
            return BodyResult(
                True,
                "mcp_discover",
                run_sync(
                    self._discover_async(name),
                    timeout_seconds=timeout,
                ),
            )
        except Exception as exc:
            return BodyResult(False, "mcp_discover", error=f"{type(exc).__name__}: {exc}")

    @classmethod
    def _content_blocks(cls, result: Any) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        raw_blocks = list(getattr(result, "content", []) or [])
        if len(raw_blocks) > cls.MAX_CONTENT_BLOCKS:
            raise ValueError("MCP response contains too many content blocks")
        remaining_chars = cls.MAX_PROJECTED_BYTES
        for block in raw_blocks:
            item: dict[str, Any] = {"type": type(block).__name__}
            for attr in ("text", "mime_type", "uri", "name"):
                if hasattr(block, attr):
                    text = str(getattr(block, attr))
                    if len(text.encode("utf-8")) > remaining_chars:
                        raise ValueError("MCP response text exceeds aggregate limit")
                    item[attr] = text[:100_000]
                    remaining_chars -= len(item[attr].encode("utf-8"))
            if hasattr(block, "data"):
                data = getattr(block, "data")
                item["data_size"] = len(data) if hasattr(data, "__len__") else None
            blocks.append(item)
        return bounded_json_value(
            blocks,
            field="MCP content blocks",
            max_bytes=cls.MAX_PROJECTED_BYTES,
        )

    @classmethod
    def _machine_object(cls, result: Any) -> dict[str, Any] | None:
        structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict):
            if set(structured) == {"result"} and isinstance(structured.get("result"), dict):
                structured = structured["result"]
            bounded = bounded_json_value(
                structured,
                field="MCP structured_content",
                max_bytes=cls.MAX_PROJECTED_BYTES,
            )
            return dict(bounded)
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if not isinstance(text, str):
                continue
            candidate = text.strip()
            if len(candidate.encode("utf-8")) > cls.MAX_PROJECTED_BYTES:
                raise ValueError("MCP JSON text block exceeds aggregate limit")
            if not candidate or not candidate.startswith("{"):
                continue
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            if set(parsed) == {"result"} and isinstance(parsed.get("result"), dict):
                parsed = parsed["result"]
            bounded = bounded_json_value(
                parsed,
                field="MCP parsed structured content",
                max_bytes=cls.MAX_PROJECTED_BYTES,
            )
            return dict(bounded)
        return None

    async def _call_async(
        self,
        server_name: str,
        tool: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        server = self._server(server_name)
        if not bool(server.get("allow_tool_calls", False)):
            raise PermissionError("MCP tool calls are disabled for this server")
        patterns = list(server.get("allowed_tools") or [])
        if not patterns or not self._allowed(tool, patterns):
            raise PermissionError(f"MCP tool is not allow-listed: {tool}")
        arguments = self._tool_arguments(server, tool, arguments)
        async with self._client(server_name) as client:
            result = await client.call_tool(tool, arguments)
            return {
                "server": server_name,
                "tool": tool,
                "is_error": bool(result.is_error),
                "structured_content": self._machine_object(result),
                "content": self._content_blocks(result),
            }

    def call(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
    ) -> BodyResult:
        try:
            timeout = self._operation_timeout(server)
            data = run_sync(
                self._call_async(server, str(tool), dict(arguments or {})),
                timeout_seconds=timeout,
            )
            return BodyResult(
                not data["is_error"],
                "mcp_call",
                data,
                error="MCP tool returned is_error=true" if data["is_error"] else None,
            )
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
            raw_contents = list(result.contents or [])
            if len(raw_contents) > self.MAX_CONTENT_BLOCKS:
                raise ValueError("MCP resource contains too many blocks")
            remaining_chars = self.MAX_PROJECTED_BYTES
            for block in raw_contents:
                item: dict[str, Any] = {"type": type(block).__name__}
                for attr in ("uri", "mime_type", "text"):
                    if hasattr(block, attr):
                        text = str(getattr(block, attr))
                        if len(text.encode("utf-8")) > remaining_chars:
                            raise ValueError("MCP resource text exceeds aggregate limit")
                        item[attr] = text[:200_000]
                        remaining_chars -= len(item[attr].encode("utf-8"))
                if hasattr(block, "blob"):
                    blob = getattr(block, "blob")
                    item["blob_size"] = len(blob) if hasattr(blob, "__len__") else None
                contents.append(item)
            return bounded_json_value(
                {"server": server_name, "uri": uri, "contents": contents},
                field="MCP resource result",
                max_bytes=self.MAX_PROJECTED_BYTES,
            )

    def read_resource(self, server: str, uri: str) -> BodyResult:
        try:
            timeout = self._operation_timeout(server)
            return BodyResult(
                True,
                "mcp_read_resource",
                run_sync(
                    self._read_resource_async(server, str(uri)),
                    timeout_seconds=timeout,
                ),
            )
        except Exception as exc:
            return BodyResult(
                False,
                "mcp_read_resource",
                error=f"{type(exc).__name__}: {exc}",
            )
