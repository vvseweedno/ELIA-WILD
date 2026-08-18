from __future__ import annotations

import time
from typing import Any

from .resource_runtime import ResourceOrganismRuntime
from .tools import ToolResult
from .work_ports import WorkPortRegistry


class ExternalWorkOrganismRuntime(ResourceOrganismRuntime):
    """Genesis 1.5 runtime: Resource Ecology + configured external work ports.

    Work-port capabilities are explicit configured authority. The replaceable model may
    choose a configured port/work item, but it cannot choose arbitrary MCP server/tool
    names or mark lifecycle transitions by narration. The port itself records observed
    submission/outcome evidence before Resource Ecology advances.
    """

    WORK_PORT_CAPABILITIES = {"submit_work", "check_work_outcome"}

    def __init__(
        self,
        *args: Any,
        mcp_target_overrides: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        config = args[0] if args else kwargs.get("config")
        if config is None:
            raise TypeError("ExternalWorkOrganismRuntime requires Config as the first argument")
        self.work_ports = WorkPortRegistry(
            config.runtime.state_dir / "workspace",
            config.raw_tools,
            mcp_target_overrides=mcp_target_overrides,
        )
        super().__init__(*args, **kwargs)

    def capability_state(self) -> dict[str, Any]:
        base = super().capability_state()
        catalog = dict(base["catalog"])
        catalog.update(self.work_ports.catalog())
        # Recompute health against the full catalog so Executive/self-model/MCP status
        # cannot disagree about a port capability merely because it is not ToolRegistry-owned.
        health = self.memory.capability_health_all(list(catalog), window=20)
        return {"catalog": catalog, "health": health}

    def _state_components(self) -> dict[str, Any]:
        components = super()._state_components()
        components["work_ports"] = self.work_ports.diagnostics()
        return components

    def _context(self) -> dict[str, Any]:
        context = super()._context()
        context["work_ports"] = self.work_ports.diagnostics()
        context["_system_prompt"] = self.prompt_template.render(context)
        return context

    def _execute_action(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name not in self.WORK_PORT_CAPABILITIES:
            return super()._execute_action(name, args)

        health = self.memory.capability_health(name, window=20)
        if (
            name not in self.DEGRADATION_EXEMPT
            and int(health["consecutive_failures"]) >= self.CAPABILITY_FAILURE_THRESHOLD
        ):
            error = (
                f"Capability temporarily suppressed after {health['consecutive_failures']} consecutive failures. "
                "Inspect the configured work port or use an alternative authorized capability instead of blind retry."
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
        result = self.work_ports.execute(name, args)
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
        report["work_ports"] = self.work_ports.diagnostics()
        report["resource_ecology"] = self._resource_ecology_snapshot()
        return report


EliaExternalWorkRuntime = ExternalWorkOrganismRuntime
