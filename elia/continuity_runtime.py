from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from .agency import AgencyKernel
from .attractor import AutonomyAttractor
from .autonomy import derive_needs
from .epistemic_runtime import EpistemicOrganismRuntime
from .transition_kernel import AcceptedTransitionGuard, TransitionRecovery


class ELIARuntime(EpistemicOrganismRuntime):
    """Canonical ELIA production runtime with crash-recoverable transitions.

    Historical Genesis runtime layers remain compatibility ancestors while the public
    runtime surface converges on this single class. A complete cognitive cycle is
    either accepted as one durable state transition or its speculative local state and
    Chronicle suffix are restored. Safety-critical external-work outbox evidence
    survives rollback and repairs its local projection afterwards.

    The canonical runtime owns a composed AgencyKernel and AutonomyAttractor. Verified
    organism needs become durable commitments before inference, so intention survives
    model calls, process exits, hibernation, and substrate replacement without gaining
    any new execution authority. Unfinished resource work is reconciled into the same
    durable agency cursor so a later wake continues the most causally advanced open work
    item instead of inventing a fresh approximation of the prior objective. Agency also
    provides a one-way wake deadline: cognition may request an earlier wake, never a
    later one than verified commitments permit.

    The attractor is explicitly advisory: it supplies a project-owned mathematical,
    cognitive and aesthetic preference field inside the feasible action set. It cannot
    authorize a capability, override CriticAssurance, mint evidence/resources, or turn a
    forbidden decision into a permitted one.

    ELIARuntime is also the final owner of the cognitive context. Historical runtime
    layers may add evidence during `_context()` or `_before_brain()`, but only the
    canonical finalizer below may bind the complete production system prompt that is
    delivered to the replaceable cognitive substrate. This prevents an older layer from
    accidentally erasing newer policy such as durable Agency or the Autonomy Attractor.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._transition_recovery: TransitionRecovery | None = None
        self._last_agency_wake_policy: dict[str, Any] | None = None
        config = args[0] if args else kwargs.get("config")
        if config is None:
            raise TypeError("ELIARuntime requires Config as the first argument")
        self.attractor = AutonomyAttractor.load(
            config.system_prompt_path.with_name("autonomy_attractor.md")
        )
        super().__init__(*args, **kwargs)
        self.agency = AgencyKernel(
            self.memory,
            max_active_goals=int(getattr(self, "MAX_ACTIVE_GOALS", 8)),
        )

    def _cognitive_policy_fingerprint(self) -> str:
        material = (
            f"prompt:{self.prompt_template.fingerprint}\n"
            f"attractor:{self.attractor.fingerprint}\n"
        )
        return sha256(material.encode("utf-8")).hexdigest()

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

        prior_attractor = self.memory.get_meta("autonomy_attractor_fingerprint", "") or ""
        self.memory.set_meta("autonomy_attractor_fingerprint", self.attractor.fingerprint)
        self.memory.set_meta(
            "cognitive_policy_fingerprint",
            self._cognitive_policy_fingerprint(),
        )
        if prior_attractor != self.attractor.fingerprint:
            self.memory.remember(
                "cognitive_policy",
                "Bound project-owned autonomy attractor to the canonical cognitive substrate.",
                importance=0.95,
                source="continuity_kernel",
                metadata={
                    "attractor_fingerprint": self.attractor.fingerprint,
                    "prompt_fingerprint": self.prompt_template.fingerprint,
                    "cognitive_policy_fingerprint": self._cognitive_policy_fingerprint(),
                },
            )

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
        parent = getattr(super(), "_after_transition_rollback", None)
        if callable(parent):
            parent()

    def _state_components(self) -> dict[str, Any]:
        """Add deployment-effective body readiness without mutating historical runtimes."""
        components = super()._state_components()
        capabilities = components.get("capabilities")
        catalog = capabilities.get("catalog") if isinstance(capabilities, dict) else None
        if not isinstance(catalog, dict):
            return components

        needs = list(components.get("needs") or [])
        names = {
            str(item.get("name", ""))
            for item in needs
            if isinstance(item, dict)
        }
        if "body_readiness" not in names:
            chronicle = components.get("chronicle") or {}
            additional = derive_needs(
                self.memory,
                chronicle_valid=bool(
                    chronicle.get("valid", False)
                    if isinstance(chronicle, dict)
                    else False
                ),
                budget=(
                    components.get("resources")
                    if isinstance(components.get("resources"), dict)
                    else self.budget()
                ),
                active_goals=(
                    components.get("goals")
                    if isinstance(components.get("goals"), list)
                    else self.memory.active_goals(16)
                ),
                capability_health=(
                    capabilities.get("health")
                    if isinstance(capabilities.get("health"), dict)
                    else {}
                ),
                capability_catalog=catalog,
                economy=(
                    components.get("economy")
                    if isinstance(components.get("economy"), dict)
                    else self.economy.snapshot(16)
                ),
            )
            body_need = next(
                (need.as_dict() for need in additional if need.name == "body_readiness"),
                None,
            )
            if body_need is not None:
                needs.append(body_need)
                needs.sort(
                    key=lambda item: (
                        -float(item.get("severity", 0.0)),
                        str(item.get("name", "")),
                    )
                )
                components["needs"] = needs[:24]
                self_model = components.get("self_model")
                if isinstance(self_model, dict):
                    self_model["needs"] = [
                        str(item.get("name", ""))
                        for item in components["needs"]
                        if isinstance(item, dict)
                    ]
        return components

    @staticmethod
    def _active_work_from_components(components: dict[str, Any]) -> list[Any]:
        resource_ecology = components.get("resource_ecology")
        if not isinstance(resource_ecology, dict):
            return []
        active = resource_ecology.get("active_work")
        return active if isinstance(active, list) else []

    def _reconcile_agency_from_components(self, components: dict[str, Any]):
        return self.agency.reconcile(
            components.get("needs") or [],
            active_work=self._active_work_from_components(components),
        )

    def _finalize_cognitive_context(self, context: dict[str, Any]) -> dict[str, Any]:
        """Bind the final canonical cognitive policy after every historical enrichment.

        Ancestor layers may render provisional prompts while adding Executive or
        epistemic evidence. The production boundary deliberately overwrites that
        provisional prompt only here, after all such enrichment, so exactly one final
        policy reaches `Brain.decide()`.
        """
        context["agency"] = self.agency.snapshot()
        context["_system_prompt"] = (
            self.prompt_template.render(context)
            + "\n\n"
            + self.attractor.text
            + "\n\nAttractor fingerprint: "
            + self.attractor.fingerprint
        )
        return context

    def _context(self) -> dict[str, Any]:
        return self._finalize_cognitive_context(super()._context())

    def _before_brain(self, context: dict[str, Any], plan: Any) -> dict[str, Any]:
        # `_before_brain` is the final enrichment hook used by Executive/Epistemic
        # ancestors. Finalize *after* their super-chain so they cannot silently erase
        # canonical Agency/Attractor policy before the model call.
        enriched = super()._before_brain(context, plan)
        return self._finalize_cognitive_context(enriched)

    def _schedule_next_wake(self, requested: float | None) -> tuple[float, str]:
        post_action_components = self._state_components()
        post_action_agency = self._reconcile_agency_from_components(post_action_components)
        model_requested = (
            float(self.config.runtime.cycle_sleep_seconds)
            if requested is None
            else float(requested)
        )
        model_requested = max(0.0, min(model_requested, 86400.0))
        policy = self.agency.wake_policy(post_action_agency.as_dict())
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
                components = self._state_components()
                agency_before = self._reconcile_agency_from_components(components)
                report = super().cycle()

                decision = report.get("decision") if isinstance(report, dict) else {}
                forecast = report.get("forecast") if isinstance(report, dict) else {}
                prediction = forecast.get("prediction") if isinstance(forecast, dict) else {}
                assurance = report.get("assurance") if isinstance(report, dict) else {}
                capabilities = components.get("capabilities")
                catalog = (
                    capabilities.get("catalog")
                    if isinstance(capabilities, dict)
                    else {}
                )
                evaluation = self.attractor.evaluate(
                    action_name=(
                        str(decision.get("action_name", ""))
                        if isinstance(decision, dict)
                        else ""
                    ),
                    prediction=(prediction if isinstance(prediction, dict) else {}),
                    agency=agency_before.as_dict(),
                    capability_catalog=(catalog if isinstance(catalog, dict) else {}),
                    assurance_accepted=(
                        bool(assurance.get("accepted"))
                        if isinstance(assurance, dict)
                        else False
                    ),
                )
                evaluation_dict = evaluation.as_dict()
                self.memory.set_meta(
                    "autonomy_attractor_last_v1",
                    json.dumps(evaluation_dict, ensure_ascii=False, sort_keys=True),
                )
                self.memory.remember(
                    "attractor_evaluation",
                    json.dumps(evaluation_dict, ensure_ascii=False, sort_keys=True),
                    importance=0.55,
                    source="autonomy_attractor",
                    metadata={
                        "score": evaluation.score,
                        "hard_constraints_satisfied": evaluation.hard_constraints_satisfied,
                        "action_name": evaluation.action_name,
                        "attractor_fingerprint": evaluation.attractor_fingerprint,
                    },
                )

                report["agency_before"] = agency_before.as_dict()
                report["agency"] = self.agency.snapshot()
                report["agency_wake_policy"] = dict(self._last_agency_wake_policy or {})
                report["autonomy_attractor"] = evaluation_dict
                report["cognitive_policy_fingerprint"] = self._cognitive_policy_fingerprint()
                transition.accept()
            return report
        except BaseException:
            try:
                self._after_transition_rollback()
            except Exception:
                pass
            raise


ContinuityKernelRuntime = ELIARuntime
EliaContinuityRuntime = ELIARuntime
