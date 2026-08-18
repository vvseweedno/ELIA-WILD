from __future__ import annotations

from itertools import count
import json
import os
from typing import Any

from .net import pinned_http_request
from .types import BodyCapability, BodyResult


class JSONRPCBody:
    """JSON-RPC 2.0 over named, preconfigured HTTP endpoints."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = dict(config or {})
        self._ids = count(1)

    def endpoints(self) -> dict[str, dict[str, Any]]:
        raw = self.config.get("endpoints") or {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(name)[:64]: dict(item)
            for name, item in raw.items()
            if isinstance(item, dict) and bool(item.get("enabled", True))
        }

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False)) and bool(self.endpoints())

    def capabilities(self) -> list[BodyCapability]:
        return [
            BodyCapability(
                "jsonrpc_call",
                "Call one allow-listed JSON-RPC 2.0 method on one configured DNS-pinned HTTP endpoint.",
                "{endpoint: configured_name, method: str, params?: object|array}",
                "configured_jsonrpc_call",
                "side effects depend on the allow-listed remote method",
                "dns_pinned_configured_http_endpoint",
                "network",
                self.enabled,
                "ready" if self.enabled else "disabled_or_no_endpoints",
            )
        ]

    @staticmethod
    def _headers(item: dict[str, Any]) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        mapping = item.get("headers_from_env") or {}
        if not isinstance(mapping, dict):
            raise ValueError("headers_from_env must be an object")
        for header, env_name in mapping.items():
            value = os.getenv(str(env_name))
            if value is None:
                raise RuntimeError(f"required JSON-RPC credential environment variable is missing: {env_name}")
            headers[str(header)] = value
        return headers

    def call(self, endpoint: str, method: str, params: Any = None) -> BodyResult:
        if not self.enabled:
            return BodyResult(False, "jsonrpc_call", error="JSON-RPC body is disabled")
        item = self.endpoints().get(str(endpoint))
        if item is None:
            return BodyResult(False, "jsonrpc_call", error=f"unknown JSON-RPC endpoint: {endpoint!r}")
        method = str(method).strip()
        allowed = {str(value) for value in item.get("allowed_methods") or []}
        if not method or method not in allowed:
            return BodyResult(False, "jsonrpc_call", error=f"JSON-RPC method is not allow-listed: {method!r}")
        url = str(item.get("url", "")).strip()
        try:
            timeout = max(0.5, min(float(item.get("timeout_seconds", 20.0)), 120.0))
            max_request_bytes = max(
                1024,
                min(int(item.get("max_request_bytes", 1_000_000)), 8_000_000),
            )
            max_response_bytes = max(
                1024,
                min(int(item.get("max_response_bytes", 1_000_000)), 8_000_000),
            )
            request_id = next(self._ids)
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params if params is not None else {},
            }
            request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            response = pinned_http_request(
                "POST",
                url,
                body=request_body,
                headers=self._headers(item),
                timeout=timeout,
                max_request_bytes=max_request_bytes,
                max_bytes=max_response_bytes,
                allow_private=bool(item.get("allow_private", False)),
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise RuntimeError(f"JSON-RPC HTTP status {response.status_code}")
            if response.truncated:
                raise ValueError("JSON-RPC response exceeded configured size limit")
            body = json.loads(response.content.decode(response.encoding or "utf-8", errors="strict"))
            if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or body.get("id") != request_id:
                raise ValueError("invalid JSON-RPC 2.0 response envelope")
            if body.get("error") is not None:
                return BodyResult(
                    False,
                    "jsonrpc_call",
                    data={"endpoint": endpoint, "method": method, "error": body["error"]},
                    error="remote JSON-RPC error",
                )
            return BodyResult(
                True,
                "jsonrpc_call",
                {
                    "endpoint": endpoint,
                    "method": method,
                    "peer_ip": response.peer_ip,
                    "result": body.get("result"),
                },
            )
        except Exception as exc:
            return BodyResult(False, "jsonrpc_call", error=f"{type(exc).__name__}: {exc}")
