from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urlparse

import httpx


@dataclass(slots=True)
class ToolResult:
    ok: bool
    tool: str
    data: Any = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolRegistry:
    def __init__(self, workspace: Path, tool_config: dict[str, Any] | None = None):
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.config = tool_config or {}

    def descriptions(self) -> dict[str, str]:
        return {
            "noop": "Do nothing this cycle. args={}",
            "list_workspace": "List files in the private workspace. args={}",
            "read_workspace": "Read one UTF-8 workspace file. args={path: str}",
            "write_workspace": "Write one UTF-8 workspace file. args={path: str, content: str}",
            "http_get": (
                "Read one public http/https URL. Private, loopback, link-local and reserved "
                "network destinations are rejected. args={url: str}"
            ),
        }

    def execute(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        args = args or {}
        try:
            if name == "noop":
                return ToolResult(True, name, {"message": "No action taken."})
            if name == "list_workspace":
                return self._list_workspace()
            if name == "read_workspace":
                return self._read_workspace(str(args.get("path", "")))
            if name == "write_workspace":
                return self._write_workspace(
                    str(args.get("path", "")), str(args.get("content", ""))
                )
            if name == "http_get":
                return self._http_get(str(args.get("url", "")))
            return ToolResult(False, name, error=f"Unknown tool: {name}")
        except Exception as exc:  # Tool failures become observations, not process failures.
            return ToolResult(False, name, error=f"{type(exc).__name__}: {exc}")

    def _safe_path(self, relative: str) -> Path:
        if not relative or relative in {".", "./"}:
            raise ValueError("A file path is required")
        candidate = (self.workspace / relative).resolve()
        if not candidate.is_relative_to(self.workspace):
            raise ValueError("Path escapes workspace")
        return candidate

    def _list_workspace(self) -> ToolResult:
        files = [
            str(path.relative_to(self.workspace))
            for path in sorted(self.workspace.rglob("*"))
            if path.is_file()
        ]
        return ToolResult(True, "list_workspace", {"files": files[:1000]})

    def _read_workspace(self, relative: str) -> ToolResult:
        path = self._safe_path(relative)
        if not path.is_file():
            return ToolResult(False, "read_workspace", error="File does not exist")
        data = path.read_text(encoding="utf-8")
        return ToolResult(True, "read_workspace", {"path": relative, "content": data[:256_000]})

    def _write_workspace(self, relative: str, content: str) -> ToolResult:
        path = self._safe_path(relative)
        if len(content.encode("utf-8")) > 256_000:
            return ToolResult(False, "write_workspace", error="Write exceeds 256 KB limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return ToolResult(
            True,
            "write_workspace",
            {"path": relative, "bytes": len(content.encode("utf-8"))},
        )

    @staticmethod
    def _validate_public_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Only http/https URLs are supported")
        if not parsed.hostname:
            raise ValueError("URL hostname is required")
        if parsed.username or parsed.password:
            raise ValueError("Credentials in URLs are not supported")

        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        for info in addresses:
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise ValueError(f"Non-public destination rejected: {ip}")

    def _http_get(self, url: str) -> ToolResult:
        http_cfg = self.config.get("http_get", {})
        if not http_cfg.get("enabled", True):
            return ToolResult(False, "http_get", error="http_get is disabled")

        self._validate_public_url(url)
        timeout = float(http_cfg.get("timeout_seconds", 20))
        max_bytes = int(http_cfg.get("max_bytes", 1_000_000))

        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.get(
                url,
                headers={"User-Agent": "ELIA-WILD/0.1 (+research-agent)"},
            )

        raw = response.content[:max_bytes]
        content_type = response.headers.get("content-type", "")
        text = raw.decode(response.encoding or "utf-8", errors="replace")
        data = {
            "url": str(response.url),
            "status_code": response.status_code,
            "content_type": content_type,
            "headers": {
                key: value
                for key, value in response.headers.items()
                if key.lower() in {"content-type", "content-length", "location", "last-modified"}
            },
            "text": text,
            "truncated": len(response.content) > max_bytes,
        }
        return ToolResult(200 <= response.status_code < 400, "http_get", data)
