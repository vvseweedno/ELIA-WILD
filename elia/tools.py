from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import ipaddress
import json
import re
import socket
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx


@dataclass(slots=True)
class ToolResult:
    ok: bool
    tool: str
    data: Any = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    description: str
    args: str
    authority: str
    side_effects: str
    network_scope: str
    cost_class: str
    enabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolRegistry:
    def __init__(self, workspace: Path, tool_config: dict[str, Any] | None = None):
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.config = tool_config or {}

    def catalog(self) -> dict[str, dict[str, Any]]:
        http_enabled = bool(self.config.get("http_get", {}).get("enabled", True))
        capabilities = [
            Capability(
                "noop",
                "Take no external action this cycle.",
                "{}",
                "none",
                "none",
                "none",
                "negligible",
            ),
            Capability(
                "list_workspace",
                "List files owned by ELIA inside the private workspace jail.",
                "{}",
                "workspace_read",
                "reads private workspace metadata",
                "none",
                "negligible",
            ),
            Capability(
                "read_workspace",
                "Read one UTF-8 file owned by ELIA inside the workspace jail.",
                "{path: str}",
                "workspace_read",
                "reads private workspace content",
                "none",
                "negligible",
            ),
            Capability(
                "write_workspace",
                "Write one UTF-8 file owned by ELIA inside the workspace jail.",
                "{path: str, content: str}",
                "workspace_write",
                "writes private workspace content",
                "none",
                "low",
            ),
            Capability(
                "http_get",
                "Read one public HTTP/HTTPS resource. Private and reserved destinations are rejected.",
                "{url: str}",
                "public_network_read",
                "remote read request only",
                "public_http_https",
                "network",
                enabled=http_enabled,
            ),
            Capability(
                "self_check",
                "Run bounded local checks of ELIA-owned workspace primitives and path-jail enforcement.",
                "{}",
                "local_self_diagnostic",
                "creates and removes one temporary workspace scratch file",
                "none",
                "low",
            ),
            Capability(
                "propose_repair",
                "Persist a structured repair proposal for later validation; does not modify runtime code or deploy anything.",
                "{title: str, diagnosis: str, proposed_change: str, validation_plan: str}",
                "workspace_write",
                "writes a proposal under workspace/repairs only",
                "none",
                "low",
            ),
        ]
        return {item.name: item.as_dict() for item in capabilities}

    def descriptions(self) -> dict[str, str]:
        return {
            name: f"{item['description']} args={item['args']} enabled={item['enabled']}"
            for name, item in self.catalog().items()
        }

    def execute(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        args = args or {}
        capability = self.catalog().get(name)
        if capability is not None and not capability["enabled"]:
            return ToolResult(False, name, error=f"Capability is disabled by configuration: {name}")
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
            if name == "self_check":
                return self._self_check()
            if name == "propose_repair":
                return self._propose_repair(args)
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

    def _self_check(self) -> ToolResult:
        token = uuid4().hex
        relative = f".selfcheck-{token}.txt"
        scratch = self._safe_path(relative)
        checks: dict[str, bool] = {}
        try:
            scratch.write_text(token, encoding="utf-8")
            checks["workspace_write"] = scratch.is_file()
            checks["workspace_read"] = scratch.read_text(encoding="utf-8") == token
            try:
                self._safe_path("../selfcheck-escape.txt")
                checks["workspace_jail"] = False
            except ValueError:
                checks["workspace_jail"] = True
        finally:
            scratch.unlink(missing_ok=True)
        checks["scratch_cleanup"] = not scratch.exists()
        ok = all(checks.values())
        return ToolResult(
            ok,
            "self_check",
            {
                "checks": checks,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "note": "No network, shell, credentials, or external systems were touched.",
            },
            error=None if ok else "One or more bounded self-checks failed",
        )

    @staticmethod
    def _slug(text: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip()).strip("-").lower()
        return (slug or "repair")[:64]

    def _propose_repair(self, args: dict[str, Any]) -> ToolResult:
        title = str(args.get("title", "")).strip()[:240]
        diagnosis = str(args.get("diagnosis", "")).strip()[:8000]
        proposed_change = str(args.get("proposed_change", "")).strip()[:16000]
        validation_plan = str(args.get("validation_plan", "")).strip()[:8000]
        if not title or not diagnosis or not proposed_change or not validation_plan:
            return ToolResult(
                False,
                "propose_repair",
                error="title, diagnosis, proposed_change, and validation_plan are required",
            )
        proposal = {
            "title": title,
            "diagnosis": diagnosis,
            "proposed_change": proposed_change,
            "validation_plan": validation_plan,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "proposal_only",
            "deployment_authority": "none",
        }
        relative = f"repairs/{self._slug(title)}-{uuid4().hex[:8]}.json"
        payload = json.dumps(proposal, ensure_ascii=False, sort_keys=True, indent=2)
        write = self._write_workspace(relative, payload)
        if not write.ok:
            return ToolResult(False, "propose_repair", error=write.error)
        return ToolResult(
            True,
            "propose_repair",
            {
                "path": relative,
                "status": "proposal_only",
                "message": "Repair proposal stored for validation; no runtime code was changed.",
            },
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
                headers={"User-Agent": "ELIA-WILD/0.4 (+research-agent)"},
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
