from __future__ import annotations

from hashlib import sha256
import json
import time
from typing import Any

from .agency import AgencyKernel
from .attractor import AutonomyAttractor
from .autonomy import derive_needs
from .epistemic_runtime import EpistemicOrganismRuntime
from .external_effects import (
    EXTERNAL_EFFECT_ACTIONS,
    ExternalEffectIndeterminate,
    ExternalEffectLedger,
)
from .memory_trust import MemoryTrustGate
from .owner_control import OwnerControl, OwnerControlError, OwnerMandate
from .pipeline import CanonicalRuntimePipeline, RuntimeStage
from .tools import ToolResult
from .transition_kernel import AcceptedTransitionGuard, TransitionRecovery


class ELIARuntime(EpistemicOrganismRuntime):
    """Canonical ELIA production runtime with crash-recoverable transitions.

    Historical Genesis runtime layers remain compatibility ancestry. Genesis 1.7.1 no
    longer adds new production behavior by deepening that inheritance tree: owner
    authority, external-effect semantics, resource ingress and final cognitive policy
    are composed through `CanonicalRuntimePipeline` stages above the historical body.

    A complete cognitive cycle is either accepted as one durable state transition or
    its speculative local state/Chronicle suffix are restored. External truth and owner
    control survive that rollback. Model-authored memory enters only through a trust
    gate, while durable Agency and the Attractor remain non-authoritative preference and
    commitment mechanisms.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._transition_recovery: TransitionRecovery | None = None
        self._last_agency_wake_policy: dict[str, Any] | None = None
        config = args[0] if args else kwargs.get("config")
        if config is None:
            raise TypeError("ELIARuntime requires Config as the first argument")

        database = config.runtime.state_dir / "memory.sqlite3"
        self.attractor = AutonomyAttractor.load(
            config.system_prompt_path.with_name("autonomy_attractor.md")
        )
        self.owner_mandate = OwnerMandate.load(
            config.system_prompt_path.with_name("owner_mandate.yaml"),
            required=False,
        )
        # These stores must exist before dynamic `_boot()` dispatch during the historical
        # super-chain so interrupted sends and owner controls can be recovered first.
        self.external_effects = ExternalEffectLedger(database)
        self.owner_control = OwnerControl(database, self.owner_mandate)
        self.resource_ingress = None

        super().__init__(*args, **kwargs)
        self.agency = AgencyKernel(
            self.memory,
            max_active_goals=int(getattr(self, "MAX_ACTIVE_GOALS", 8)),
        )
        self.memory_trust = MemoryTrustGate(self.memory)

        ingress_config = dict((self.config.raw_tools or {}).get("resource_ingress") or {})
        if bool(ingress_config.get("enabled", False)):
            from .resource_ingress_hardened import AttestedResourceIngressRegistry

            self.resource_ingress = AttestedResourceIngressRegistry(
                self.config.runtime.state_dir,
                self.config.raw_tools,
            )

        self.pipeline = CanonicalRuntimePipeline(
            [
                RuntimeStage(
                    "owner_authority",
                    before_cycle=self.owner_control.assert_runtime_allowed,
                    enrich_context=self._owner_context_stage,
                    execute_action=self._owner_action_stage,
                ),
                RuntimeStage(
                    "resource_ingress",
                    execute_action=self._resource_ingress_action_stage,
                ),
                RuntimeStage(
                    "external_effect_ledger",
                    enrich_context=self._effect_context_stage,
                    execute_action=self._external_effect_action_stage,
                ),
                RuntimeStage(
                    "cognitive_policy_finalizer",
                    enrich_context=self._policy_finalizer_stage,
                ),
            ]
        )

    def _cognitive_policy_fingerprint(self) -> str:
        material = (
            f"prompt:{self.prompt_template.fingerprint}\n"
            f"attractor:{self.attractor.fingerprint}\n"
            f"owner_mandate:{self.owner_mandate.fingerprint}\n"
        )
        return sha256(material.encode("utf-8")).hexdigest()

    def _boot(self) -> None:
        recovery = AcceptedTransitionGuard.recover_incomplete(
            self.config.runtime.state_dir,
            self.chronicle,
        )
        self._transition_recovery = recovery
        super()._boot()

        unresolved = self.external_effects.recover_interrupted()
        if unresolved:
            self.memory.remember(
                "external_effect_recovery",
                f"Recovered {len(unresolved)} interrupted external effect(s) as indeterminate.",
                importance=1.0,
                source="continuity_kernel",
                metadata={
                    "effect_ids": [item.effect_id for item in unresolved[:32]],
                    "rule": "reconcile remote state before any matching retry",
                },
            )

        prior_attractor = self.memory.get_meta("autonomy_attractor_fingerprint", "") or ""
        self.memory.set_meta("autonomy_attractor_fingerprint", self.attractor.fingerprint)
        self.memory.set_meta("owner_mandate_fingerprint", self.owner_mandate.fingerprint)
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
                    "owner_mandate_fingerprint": self.owner_mandate.fingerprint,
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
        parent = getattr(super(), "_after_transition_rollback", None)
        if callable(parent):
            parent()

    @staticmethod
    def _public_effect_state(raw: dict[str, Any]) -> dict[str, Any]:
        unresolved = []
        for item in list(raw.get("unresolved") or [])[:16]:
            if not isinstance(item, dict):
                continue
            unresolved.append(
                {
                    "effect_id": str(item.get("effect_id", ""))[:64],
                    "action_name": str(item.get("action_name", ""))[:128],
                    "status": str(item.get("status", ""))[:64],
                    "risk_class": str(item.get("risk_class", ""))[:128],
                }
            )
        return {
            "unresolved_count": int(raw.get("unresolved_count", 0) or 0),
            "unresolved": unresolved,
            "rule": "Indeterminate external effects must be reconciled before matching retry.",
        }

    def _state_components(self) -> dict[str, Any]:
        components = super()._state_components()
        capabilities = components.get("capabilities")
        catalog = capabilities.get("catalog") if isinstance(capabilities, dict) else None
        if not isinstance(catalog, dict):
            return components

        if self.resource_ingress is not None:
            catalog.update(self.resource_ingress.catalog())

        owner = self.owner_control.snapshot()
        approval_required = set(owner.get("approval_required_actions") or [])
        for name, raw in catalog.items():
            if not isinstance(raw, dict):
                continue
            if name in EXTERNAL_EFFECT_ACTIONS:
                raw["requires_owner_lease"] = bool(owner.get("external_lease_required"))
                raw["delegation_revoked"] = bool(owner.get("delegation_revoked"))
            if name in approval_required:
                raw["requires_human_approval"] = True
        capabilities["catalog"] = catalog
        components["owner_control"] = owner
        components["external_effects"] = self._public_effect_state(
            self.external_effects.diagnostics()
        )
        if self.resource_ingress is not None:
            ingress_diag = self.resource_ingress.diagnostics()
            components["resource_ingress"] = {
                "enabled": bool(ingress_diag.get("enabled")),
                "readiness": ingress_diag.get("readiness"),
                "verifiers": sorted(dict(ingress_diag.get("verifiers") or {})),
            }

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

    # ---- composition stages -------------------------------------------------

    def _owner_context_stage(self, context: dict[str, Any]) -> dict[str, Any]:
        identity_contract = context.get("identity_contract")
        if isinstance(identity_contract, dict):
            identity_contract["owner_mandate"] = self.owner_control.snapshot()
        return context

    def _effect_context_stage(self, context: dict[str, Any]) -> dict[str, Any]:
        capabilities = context.get("capabilities")
        if isinstance(capabilities, dict):
            capabilities["external_effects"] = self._public_effect_state(
                self.external_effects.diagnostics()
            )
        return context

    def _policy_finalizer_stage(self, context: dict[str, Any]) -> dict[str, Any]:
        context["agency"] = self.agency.snapshot()
        if hasattr(self, "pipeline"):
            identity_contract = context.get("identity_contract")
            if isinstance(identity_contract, dict):
                identity_contract["runtime_pipeline"] = self.pipeline.describe()
        context["_system_prompt"] = (
            self.prompt_template.render(context)
            + "\n\n"
            + self.attractor.text
            + "\n\nAttractor fingerprint: "
            + self.attractor.fingerprint
            + "\nOwner mandate fingerprint: "
            + self.owner_mandate.fingerprint
        )
        return context

    def _owner_action_stage(
        self,
        name: str,
        args: dict[str, Any],
        next_action,
    ) -> ToolResult:
        if name not in EXTERNAL_EFFECT_ACTIONS:
            return next_action(name, args)
        try:
            self.owner_control.assert_external_authorized(name, args)
        except OwnerControlError as exc:
            self.memory.record_capability_event(
                name,
                ok=False,
                executed=False,
                error=str(exc),
            )
            return ToolResult(False, name, data={"owner_controlled": True}, error=str(exc))
        return next_action(name, args)

    def _resource_ingress_action_stage(
        self,
        name: str,
        args: dict[str, Any],
        next_action,
    ) -> ToolResult:
        if name != "check_resource_ingress":
            return next_action(name, args)
        if self.resource_ingress is None:
            return ToolResult(False, name, error="resource ingress is not configured")
        started = time.monotonic()
        result = self.resource_ingress.execute(name, args)
        self.memory.record_capability_event(
            name,
            ok=result.ok,
            executed=True,
            duration_ms=(time.monotonic() - started) * 1000.0,
            error=result.error or "",
        )
        return result

    def _external_effect_action_stage(
        self,
        name: str,
        args: dict[str, Any],
        next_action,
    ) -> ToolResult:
        if name not in EXTERNAL_EFFECT_ACTIONS:
            return next_action(name, args)

        unresolved = self.external_effects.unresolved_for(name, args)
        if unresolved is not None and unresolved.status in {"sending", "indeterminate"}:
            error = (
                "Matching external effect is indeterminate; reconcile remote state before retry: "
                + unresolved.effect_id
            )
            self.memory.record_capability_event(
                name,
                ok=False,
                executed=False,
                error=error,
            )
            return ToolResult(
                False,
                name,
                data={"external_effect_id": unresolved.effect_id, "indeterminate": True},
                error=error,
            )

        try:
            intent = self.external_effects.prepare(name, args)
        except ExternalEffectIndeterminate as exc:
            self.memory.record_capability_event(
                name,
                ok=False,
                executed=False,
                error=str(exc),
            )
            return ToolResult(
                False,
                name,
                data={"external_effect_id": exc.record.effect_id, "indeterminate": True},
                error=str(exc),
            )

        self.external_effects.mark_sending(intent.effect_id)
        try:
            result = next_action(name, args)
        except BaseException as exc:
            self.external_effects.mark_indeterminate(
                intent.effect_id,
                f"{type(exc).__name__}: {str(exc)[:2000]}",
            )
            raise

        no_effect_proven = bool(
            isinstance(result.data, dict) and result.data.get("suppressed") is True
        )
        effect = self.external_effects.record_result(
            intent.effect_id,
            ok=result.ok,
            result=result.as_dict(),
            no_effect_proven=no_effect_proven,
        )
        self.memory.set_meta("last_external_effect_id", effect.effect_id)
        self.memory.set_meta("last_external_effect_status", effect.status)
        return result

    # ---- canonical hooks ----------------------------------------------------

    def _context(self) -> dict[str, Any]:
        context = super()._context()
        if not hasattr(self, "pipeline"):
            # Dynamic dispatch during historical initialization may reach `_context`
            # before canonical stage construction; bind minimum safe final policy.
            return self._policy_finalizer_stage(self._effect_context_stage(self._owner_context_stage(context)))
        return self.pipeline.enrich(context)

    def _before_brain(self, context: dict[str, Any], plan: Any) -> dict[str, Any]:
        enriched = super()._before_brain(context, plan)
        return self.pipeline.enrich(enriched)

    def _store_model_memories(self, decision: Any) -> list[int]:
        ids: list[int] = []
        for item in list(getattr(decision, "memories", []) or [])[:8]:
            if not isinstance(item, dict):
                continue
            memory_id = self.memory_trust.remember_from_brain(
                item,
                identity_fingerprint=self.identity.fingerprint,
                model_id=self.config.brain.model_id,
            )
            if memory_id is not None:
                ids.append(memory_id)
        return ids

    def _execute_action(self, name: str, args: dict[str, Any]) -> ToolResult:
        return self.pipeline.execute(name, args, super()._execute_action)

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
        self.pipeline.run_before_cycle()
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
                report["owner_control"] = self.owner_control.snapshot()
                report["external_effects"] = self._public_effect_state(
                    self.external_effects.diagnostics()
                )
                report["runtime_pipeline"] = self.pipeline.describe()
                if self.resource_ingress is not None:
                    ingress_diag = self.resource_ingress.diagnostics()
                    report["resource_ingress"] = {
                        "enabled": bool(ingress_diag.get("enabled")),
                        "readiness": ingress_diag.get("readiness"),
                    }
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
