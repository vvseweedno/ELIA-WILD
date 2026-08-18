from __future__ import annotations

import json
import time
from typing import Any

from .epistemic import CognitiveBiographyStore, EpistemicRegistry
from .epistemic_views import EpistemicViewStore, ResilientEpistemicCortex
from .executive import ExecutivePlan
from .external_work_runtime import ExternalWorkOrganismRuntime


class EpistemicOrganismRuntime(ExternalWorkOrganismRuntime):
    """Genesis 1.6 runtime: one Self with differentiated temporary cognitive organs.

    ACDS runs only inside an Executive-approved brain wake. Its organs are not agents
    with independent authority or identity. They receive deliberately different,
    privacy-bounded evidence views and produce compact evidence packets. An
    identity-neutral adjudicator preserves disagreement; the single ELIA Self then
    makes the ordinary one-action decision through the existing assurance boundary.

    Epistemic deliberation is a cognitive enhancement, not a single point of failure:
    individual organs and the adjudicator may degrade without granting authority or
    crashing the organism merely because one perspective failed to answer.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        config = args[0] if args else kwargs.get("config")
        if config is None:
            raise TypeError("EpistemicOrganismRuntime requires Config as the first argument")
        database = config.runtime.state_dir / "memory.sqlite3"
        self.epistemic_registry = EpistemicRegistry.load(config.epistemic_path)
        self.epistemic_store = CognitiveBiographyStore(database)
        self.epistemic_view_store = EpistemicViewStore(database)
        self.epistemic_cortex = ResilientEpistemicCortex(
            self.epistemic_registry,
            self.epistemic_store,
            self.epistemic_view_store,
        )
        self._current_epistemic_session_id: str | None = None
        self._current_epistemic_result: dict[str, Any] | None = None
        super().__init__(*args, **kwargs)

    def _state_components(self) -> dict[str, Any]:
        components = super()._state_components()
        components["epistemic_ecosystem"] = self.epistemic_cortex.snapshot()
        return components

    def _context(self) -> dict[str, Any]:
        context = super()._context()
        context["epistemic_ecosystem"] = self.epistemic_cortex.snapshot()
        context["_system_prompt"] = self.prompt_template.render(context)
        return context

    def _before_brain(self, context: dict[str, Any], plan: ExecutivePlan) -> dict[str, Any]:
        context = super()._before_brain(context, plan)
        if not plan.cognitive_budget.wake_brain:
            return context

        if not self.epistemic_cortex.should_deliberate(context):
            result = {
                "enabled": self.epistemic_registry.policy.enabled,
                "triggered": False,
                "reason": "Current Executive tier and world state do not require differentiated deliberation.",
                "biographies": self.epistemic_cortex.biography_snapshot(),
            }
            self._current_epistemic_result = result
            context["epistemic"] = result
            context["_system_prompt"] = self.prompt_template.render(context)
            return context

        brain = self._get_brain()
        started = time.monotonic()
        try:
            result = self.epistemic_cortex.deliberate(brain, context)
        finally:
            elapsed = max(0.0, time.monotonic() - started)
            # Every organ and adjudicator call consumes the same scarce cognitive
            # resource ledger as the final Self decision.
            self.memory.add_brain_seconds(elapsed)
            self._account_runtime()

        self._current_epistemic_result = result
        session_id = result.get("session_id") if isinstance(result, dict) else None
        self._current_epistemic_session_id = str(session_id) if session_id else None
        context["epistemic"] = result
        context["_system_prompt"] = self.prompt_template.render(context)
        return context

    def cycle(self) -> dict[str, Any]:
        self._current_epistemic_session_id = None
        self._current_epistemic_result = None
        try:
            report = super().cycle()
        except BaseException:
            # An interrupted session remains unresolved evidence rather than being
            # silently relabelled as a failed hypothesis.
            raise

        session_id = self._current_epistemic_session_id
        if session_id:
            result = report.get("result") or {}
            decision = report.get("decision") or {}
            observation = result.get("observation") if isinstance(result, dict) else None
            evidence = {
                "chronicle_seq": report.get("chronicle_seq"),
                "observation": observation,
                "next_wake_at": report.get("next_wake_at"),
                "result_tool": result.get("tool") if isinstance(result, dict) else None,
            }
            self.epistemic_store.resolve_session(
                session_id,
                result_ok=bool(result.get("ok")) if isinstance(result, dict) else False,
                action_name=str(decision.get("action_name", "")) if isinstance(decision, dict) else "",
                outcome_evidence=json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str),
            )

        report["epistemic"] = self._current_epistemic_result or {
            "enabled": self.epistemic_registry.policy.enabled,
            "triggered": False,
        }
        report["epistemic_biographies"] = self.epistemic_cortex.biography_snapshot()
        return report


EliaEpistemicRuntime = EpistemicOrganismRuntime
