from __future__ import annotations

import json
from typing import Any

from .agency import AgencyKernel
from .epistemic_runtime import EpistemicOrganismRuntime
from .transition_kernel import AcceptedTransitionGuard, TransitionRecovery


class ELIARuntime(EpistemicOrganismRuntime):
    """Canonical ELIA production runtime with crash-recoverable transitions.

    Historical Genesis runtime layers remain compatibility ancestors while the public
    runtime surface converges on this single class. A complete cognitive cycle is
    either accepted as one durable state transition or its speculative local state and
    Chronicle suffix are restored. Safety-critical external-work outbox evidence
    survives rollback and repairs its local projection afterwards.

    The canonical runtime also owns a composed AgencyKernel. Verified organism needs
    become durable commitments before inference, so intention survives model calls,
    process exits, hibernation, and substrate replacement without gaining any new
    execution authority. Unfinished resource work is reconciled into the same durable
    agency cursor so a later wake continues the most causally advanced open work item
    instead of inventing a fresh approximation of the prior objective. Agency also
    provides a one-way wake deadline: cognition may request an earlier wake, never a
    later one than verified commitments permit.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._transition_recovery: TransitionRecovery | None = None
        self._last_agency_wake_policy: dict[str, Any] | None = None
        super().__init__(*args, **kwargs)
        self.agency = AgencyKernel(
            self.memory,
            max_active_goals=int(getattr(self, "MAX_ACTIVE_GOALS", 8)),
        )

    def _boot(self) -> None:
        # EliaRuntime has already initialized the Chronicle and state stores when its
        # dynamic `_boot` dispatch reaches this method. Recover *before* ordinary boot
        # increments counters, appends lineage, or reconciles StateBus transactions.
        recovery = AcceptedTransitionGuard.recover_incomplete(
            self.config.runtime.state_dir,
            self.chronicle,
        )
        self._transition_recovery = recovery
        super()._boot()
        if recovery.recovered:
            self.memory.remember(
                "accepted_transition_recovery",
                "Recovered an interrupted production transition to its prior accepted state.",
                importance=1.0,
                source="continuity_kernel",
                metadata=recovery.as_dict(),
            )
            self.chronicle.append(
                "ACCEPTED_TRANSITION_RECOVERY",
                recovery.as_dict(),
            )
            self._after_transition_rollback()

    def _after_transition_rollback(self) -> None:
        """Hook inherited/extended by external-work layers for projection repair."""
        # ExternalWorkOrganismRuntime defines the meaningful implementation. Keeping a
        # fallback here makes this class robust if the ancestry is later refactored.
        parent = getattr(super(), "_after_transition_rollback", None)
        if callable(parent):
            parent()

    def _context(self) -> dict[str, Any]:
        context = super()._context()
        # Reconciliation is performed once at the beginning of the accepted transition.
        # Here the brain receives only the already-durable agency state. Re-render after
        # adding it so PromptTemplate can expose the commitment without raw private data.
        context["agency"] = self.agency.snapshot()
        context["_system_prompt"] = self.prompt_template.render(context)
        return context

    def _schedule_next_wake(self, requested: float | None) -> tuple[float, str]:
        # The substrate may propose how long to sleep, but verified commitments own the
        # upper bound. This prevents one hallucinated/over-conservative decision from
        # indefinitely abandoning an accepted external outcome or repair obligation.
        model_requested = (
            float(self.config.runtime.cycle_sleep_seconds)
            if requested is None
            else float(requested)
        )
        model_requested = max(0.0, min(model_requested, 86400.0))
        policy = self.agency.wake_policy()
        cap = policy.get("max_sleep_seconds")
        effective = model_requested
        if cap is not None:
            effective = min(effective, max(0.0, float(cap)))
        delay, wake_at = super()._schedule_next_wake(effective)
        audited = {
            **policy,
            "model_requested_sleep_seconds": model_requested,
            "effective_sleep_seconds": delay,
            "next_wake_at": wake_at,
        }
        self._last_agency_wake_policy = audited
        self.memory.set_meta(
            "agency_wake_policy_v1",
            json.dumps(audited, ensure_ascii=False, sort_keys=True),
        )
        return delay, wake_at

    def cycle(self) -> dict[str, Any]:
        guard = AcceptedTransitionGuard(self.config.runtime.state_dir, self.chronicle)
        try:
            with guard as transition:
                # Agency reconciliation is part of the same atomic transition as the
                # cognitive cycle. If later cognition/action fails, newly formed or
                # resolved commitments and the continuation cursor roll back with the
                # speculative state.
                components = self._state_components()
                resource_ecology = components.get("resource_ecology")
                active_work = (
                    resource_ecology.get("active_work") or []
                    if isinstance(resource_ecology, dict)
                    else []
                )
                agency = self.agency.reconcile(
                    components.get("needs") or [],
                    active_work=active_work,
                )
                report = super().cycle()
                report["agency"] = agency.as_dict()
                report["agency_wake_policy"] = dict(self._last_agency_wake_policy or {})
                transition.accept()
            return report
        except BaseException:
            # Context-manager exit has restored the accepted state before this hook.
            # Repair any safety-preserved external-work projection without resending.
            try:
                self._after_transition_rollback()
            except Exception:
                # Never mask the original cycle failure with a best-effort projection
                # repair error. The preserved outbox remains available on next boot.
                pass
            raise


# Compatibility names for code written against Genesis 1.7 before runtime consolidation.
ContinuityKernelRuntime = ELIARuntime
EliaContinuityRuntime = ELIARuntime
