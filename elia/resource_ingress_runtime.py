from __future__ import annotations

import time
from typing import Any

from .external_work_runtime import ExternalWorkOrganismRuntime
from .resource_ingress import ResourceIngressRegistry
from .resource_ingress_status import public_resource_ingress
from .tools import ToolResult


class ResourceIngressOrganismRuntime(ExternalWorkOrganismRuntime):
    """Genesis 1.6 runtime: external work organism + independent resource verifier.

    The verifier is a separate configured authority from work submission/outcome ports.
    It may only read a fixed provider source and ingest provider-observed positive
    resources through the signed Economy verification boundary. The model cannot
    supply amount, unit, external event id, evidence, authority, signature or key.
    """

    INGRESS_CAPABILITIES = {"check_resource_ingress"}

    def __init__(
        self,
        *args: Any,
        mcp_target_overrides: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        config = args[0] if args else kwargs.get("config")
        if config is None:
            raise TypeError("ResourceIngressOrganismRuntime requires Config as the first argument")
        # Initialize before super(): boot uses virtual capability/state hooks.
        self.resource_ingress = ResourceIngressRegistry(
            config.runtime.state_dir,
            config.raw_tools,
            mcp_target_overrides=mcp_target_overrides,
        )
        super().__init__(
            *args,
            mcp_target_overrides=mcp_target_overrides,
            **kwargs,
        )

    def capability_state(self) -> dict[str, Any]:
        base = super().capability_state()
        catalog = dict(base["catalog"])
        catalog.update(self.resource_ingress.catalog())
        health = self.memory.capability_health_all(list(catalog), window=20)
        return {"catalog": catalog, "health": health}

    def _state_components(self) -> dict[str, Any]:
        components = super()._state_components()
        components["resource_ingress"] = public_resource_ingress(
            self.resource_ingress.diagnostics()
        )
        return components

    def _context(self) -> dict[str, Any]:
        context = super()._context()
        context["resource_ingress"] = public_resource_ingress(
            self.resource_ingress.diagnostics()
        )
        context["_system_prompt"] = self.prompt_template.render(context)
        return context

    def _execute_action(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name not in self.INGRESS_CAPABILITIES:
            return super()._execute_action(name, args)

        health = self.memory.capability_health(name, window=20)
        if (
            name not in self.DEGRADATION_EXEMPT
            and int(health["consecutive_failures"]) >= self.CAPABILITY_FAILURE_THRESHOLD
        ):
            error = (
                f"Capability temporarily suppressed after {health['consecutive_failures']} consecutive failures. "
                "Inspect the configured independent verifier instead of blindly retrying."
            )
            self.memory.record_capability_event(
                name,
                ok=False,
                executed=False,
                duration_ms=0.0,
                error=error,
            )
            return ToolResult(False, name, {"suppressed": True, "health": health}, error)

        started = time.monotonic()
        result = self.resource_ingress.execute(name, args)
        duration_ms = (time.monotonic() - started) * 1000.0
        self.memory.record_capability_event(
            name,
            ok=result.ok,
            executed=True,
            duration_ms=duration_ms,
            error=result.error or "",
        )
        return result

    def cycle(self) -> dict[str, Any]:
        report = super().cycle()
        # A successful ingress may have changed verified balances/runway and work
        # realization. Refresh all downstream physiological/resource projections.
        report["resource_ingress"] = public_resource_ingress(
            self.resource_ingress.diagnostics()
        )
        report["metabolism"] = self._metabolism_snapshot()
        report["resource_ecology"] = self._resource_ecology_snapshot()
        return report


EliaResourceIngressRuntime = ResourceIngressOrganismRuntime
