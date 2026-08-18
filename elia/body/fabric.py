from __future__ import annotations

from pathlib import Path
from typing import Any

from .browser import BrowserBody
from .mcp import MCPBody
from .process import BoundedProcessRunner
from .protocols import JSONRPCBody
from .types import BodyCapability, BodyResult


class SensorimotorFabric:
    """Composes ELIA's configured digital body behind one capability contract."""

    def __init__(
        self,
        workspace: Path,
        config: dict[str, Any] | None = None,
        *,
        mcp_target_overrides: dict[str, Any] | None = None,
    ):
        self.workspace = Path(workspace).resolve()
        self.config = dict(config or {})
        self.browser = BrowserBody(self.workspace, self.config.get("browser"))
        self.process = BoundedProcessRunner(self.workspace, self.config.get("process"))
        self.mcp = MCPBody(self.config.get("mcp"), target_overrides=mcp_target_overrides)
        self.jsonrpc = JSONRPCBody(self.config.get("jsonrpc"))

    def capabilities(self) -> dict[str, dict[str, Any]]:
        items: list[BodyCapability] = []
        for adapter in (self.browser, self.process, self.mcp, self.jsonrpc):
            items.extend(adapter.capabilities())
        return {item.name: item.as_dict() for item in items}

    def execute(self, name: str, args: dict[str, Any] | None = None) -> BodyResult:
        args = dict(args or {})
        capability = self.capabilities().get(name)
        if capability is None:
            return BodyResult(False, name, error=f"unknown body capability: {name}")
        if not capability["enabled"]:
            return BodyResult(False, name, error=f"body capability is disabled/unavailable: {name} ({capability['readiness']})")
        try:
            if name == "browser_navigate":
                return self.browser.navigate(str(args.get("url", "")))
            if name == "browser_snapshot":
                return self.browser.snapshot()
            if name == "browser_click":
                return self.browser.click(dict(args.get("locator") or {}))
            if name == "browser_fill":
                return self.browser.fill(dict(args.get("locator") or {}), str(args.get("value", "")))
            if name == "browser_screenshot":
                return self.browser.screenshot(bool(args.get("full_page", False)))
            if name == "process_run":
                return self.process.run(args)
            if name == "mcp_discover":
                return self.mcp.discover(str(args.get("server", "")))
            if name == "mcp_call":
                return self.mcp.call(
                    str(args.get("server", "")),
                    str(args.get("tool", "")),
                    dict(args.get("arguments") or {}),
                )
            if name == "mcp_read_resource":
                return self.mcp.read_resource(str(args.get("server", "")), str(args.get("uri", "")))
            if name == "jsonrpc_call":
                return self.jsonrpc.call(
                    str(args.get("endpoint", "")),
                    str(args.get("method", "")),
                    args.get("params"),
                )
            return BodyResult(False, name, error=f"body dispatch is not implemented: {name}")
        except Exception as exc:
            return BodyResult(False, name, error=f"{type(exc).__name__}: {exc}")

    def close(self) -> None:
        self.browser.close()

    def diagnostics(self) -> dict[str, Any]:
        catalog = self.capabilities()
        return {
            "capability_count": len(catalog),
            "enabled": sorted(name for name, item in catalog.items() if item["enabled"]),
            "unavailable": {
                name: item["readiness"]
                for name, item in catalog.items()
                if not item["enabled"]
            },
        }
