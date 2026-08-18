from __future__ import annotations

from typing import Any

from .homeostasis import HomeostasisEngine
from .metabolism import MetabolismEngine
from .organism_runtime import OrganismRuntime


class MetabolicOrganismRuntime(OrganismRuntime):
    """Genesis 1.2 runtime: Genesis 1.1 organism + measured resource metabolism.

    This evolutionary layer leaves the tested sensorimotor/world runtime intact and
    changes only physiology/context wiring. Runway comes from verified ledger state,
    verified obligations and actual runtime metrics; no model-authored balance or
    unverified obligation can create resource solvency/scarcity here.
    """

    def _metabolism_snapshot(self) -> dict[str, Any]:
        database = self.config.runtime.state_dir / "memory.sqlite3"
        return MetabolismEngine(
            database,
            weekly_gpu_budget_hours=self.config.runtime.weekly_gpu_budget_hours,
        ).snapshot().as_dict()

    def _homeostasis_snapshot(self) -> dict[str, Any]:
        active_tx = getattr(self, "_active_cycle_transaction_id", None)
        ignored = {active_tx} if active_tx else set()
        metabolism = self._metabolism_snapshot()
        return HomeostasisEngine(
            self.config.runtime.state_dir,
            self.tools.observations,
            self.tools.world_model,
            self.tools.state_bus,
            self.tools.body.diagnostics(),
            metabolism_snapshot=metabolism,
        ).evaluate(ignore_transaction_ids=ignored).as_dict()

    def _state_components(self) -> dict[str, Any]:
        # Dynamic dispatch means OrganismRuntime already derives its homeostatic
        # needs using this class's metabolism-aware _homeostasis_snapshot().
        components = super()._state_components()
        homeostasis = components.get("homeostasis") or {}
        components["metabolism"] = homeostasis.get("metabolism") or self._metabolism_snapshot()
        return components

    def _context(self) -> dict[str, Any]:
        context = super()._context()
        homeostasis = context.get("homeostasis") or {}
        context["metabolism"] = homeostasis.get("metabolism") or self._metabolism_snapshot()
        context["_system_prompt"] = self.prompt_template.render(context)
        return context

    def cycle(self) -> dict[str, Any]:
        report = super().cycle()
        homeostasis = report.get("homeostasis") or {}
        report["metabolism"] = homeostasis.get("metabolism") or self._metabolism_snapshot()
        return report
