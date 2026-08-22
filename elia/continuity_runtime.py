from __future__ import annotations

from hashlib import sha256
import json
import time
from typing import Any

from .agency import AgencyKernel
from .attractor import AutonomyAttractor
from .autonomy import derive_needs
from .brain import Decision
from .checkpoint import recover_interrupted_restore
from .epistemic_runtime import EpistemicOrganismRuntime
from .external_effects import (
    EXTERNAL_EFFECT_ACTIONS,
    EXTERNAL_IO_ACTIONS,
    ExternalEffectIndeterminate,
    ExternalEffectLedger,
)
from .memory_trust import MemoryTrustGate
from .memory import GoalRecord
from .owner_control import OwnerControl, OwnerControlError, OwnerMandate
from .pipeline import CanonicalRuntimePipeline, RuntimeStage
from .tools import ToolResult
from .transition_kernel import AcceptedTransitionGuard, StateWriterLock, TransitionRecovery


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
        self._pre_action_agency: dict[str, Any] = {}
        self._pre_action_capability_catalog: dict[str, Any] = {}
        self._pending_attractor_candidate: dict[str, Any] | None = None
        self._pending_attractor_evaluation: Any | None = None
        self._pending_assurance_accepted: bool | None = None
        self._constructor_writer_lock_held = False
        config = args[0] if args else kwargs.get("config")
        if config is None:
            raise TypeError("ELIARuntime requires Config as the first argument")

        state_dir = config.runtime.state_dir
        # Recovery and *all* durable construction share one writer lease. Releasing it
        # after recovery but before the super-chain boots would let another process
        # atomically replace the state directory underneath already-opened stores.
        with StateWriterLock(state_dir):
            self._constructor_writer_lock_held = True
            try:
                recover_interrupted_restore(state_dir, lock_held=True)
                self._initialize_under_writer_lock(config, *args, **kwargs)
            finally:
                self._constructor_writer_lock_held = False

    def _initialize_under_writer_lock(
        self,
        config: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Construct every durable dependency while the state writer is held."""

        state_dir = config.runtime.state_dir
        database = state_dir / "memory.sqlite3"
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
            lock_held=self._constructor_writer_lock_held,
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
        capabilities_value = components.get("capabilities")
        if not isinstance(capabilities_value, dict):
            return components
        capabilities: dict[str, Any] = capabilities_value
        catalog_value = capabilities.get("catalog")
        if not isinstance(catalog_value, dict):
            return components
        catalog: dict[str, dict[str, Any]] = {
            str(name): raw
            for name, raw in catalog_value.items()
            if isinstance(raw, dict)
        }

        if self.resource_ingress is not None:
            catalog.update(self.resource_ingress.catalog())

        owner = self.owner_control.snapshot()
        approval_required = set(owner.get("approval_required_actions") or [])
        for name, raw in catalog.items():
            if not isinstance(raw, dict):
                continue
            if name in EXTERNAL_IO_ACTIONS:
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
            chronicle_value = components.get("chronicle")
            chronicle: dict[str, Any] = (
                chronicle_value if isinstance(chronicle_value, dict) else {}
            )
            resources_value = components.get("resources")
            budget: dict[str, float]
            if isinstance(resources_value, dict):
                try:
                    budget = {
                        str(key): float(raw)
                        for key, raw in resources_value.items()
                    }
                except (TypeError, ValueError):
                    budget = self.budget()
            else:
                budget = self.budget()
            goals_value = components.get("goals")
            active_goals = (
                [goal for goal in goals_value if isinstance(goal, GoalRecord)]
                if isinstance(goals_value, list)
                else self.memory.active_goals(16)
            )
            health_value = capabilities.get("health")
            capability_health: dict[str, dict[str, Any]] = (
                {
                    str(name): raw
                    for name, raw in health_value.items()
                    if isinstance(raw, dict)
                }
                if isinstance(health_value, dict)
                else {}
            )
            economy_value = components.get("economy")
            economy: dict[str, Any] = (
                economy_value
                if isinstance(economy_value, dict)
                else self.economy.snapshot(16)
            )
            additional = derive_needs(
                self.memory,
                chronicle_valid=bool(chronicle.get("valid", False)),
                budget=budget,
                active_goals=active_goals,
                capability_health=capability_health,
                capability_catalog=catalog,
                economy=economy,
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
        if name not in EXTERNAL_IO_ACTIONS:
            capability = self._pre_action_capability_catalog.get(name)
            self._evaluate_pre_action_candidate(
                name,
                args,
                authority_accepted=bool(
                    isinstance(capability, dict) and capability.get("enabled") is True
                ),
            )
            return next_action(name, args)
        try:
            self.owner_control.assert_external_authorized(name, args)
        except OwnerControlError as exc:
            self._evaluate_pre_action_candidate(
                name,
                args,
                authority_accepted=False,
            )
            self.memory.record_capability_event(
                name,
                ok=False,
                executed=False,
                error=str(exc),
            )
            return ToolResult(False, name, data={"owner_controlled": True}, error=str(exc))
        self._evaluate_pre_action_candidate(
            name,
            args,
            authority_accepted=True,
        )
        return next_action(name, args)

    @staticmethod
    def _canonical_action_args(value: Any) -> str:
        try:
            return json.dumps(
                value if isinstance(value, dict) else {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("action arguments are not finite canonical JSON") from exc

    def _evaluate_pre_action_candidate(
        self,
        name: str,
        args: dict[str, Any],
        *,
        authority_accepted: bool,
    ) -> None:
        """Bind one evaluation to the exact action crossing the pipeline.

        This hook runs in the first pipeline stage, after owner/delegation preflight for
        external I/O and before any body, network, process or resource call.
        """

        pending = self._pending_attractor_candidate
        if not isinstance(pending, dict):
            raise RuntimeError("pre-action Attractor candidate was not prepared")
        if (
            str(pending.get("action_name", "")) != str(name)
            or self._canonical_action_args(pending.get("action_args"))
            != self._canonical_action_args(args)
        ):
            raise RuntimeError("pre-action candidate does not match selected action")
        candidate = {
            **pending,
            "authority_accepted": authority_accepted is True,
        }
        evaluations = self.attractor.evaluate_pre_action_candidates(
            [candidate],
            agency=self._pre_action_agency,
            capability_catalog=self._pre_action_capability_catalog,
        )
        if len(evaluations) != 1:
            raise RuntimeError("Attractor did not return the selected pre-action candidate")
        selected = evaluations[0]
        expected = self.attractor.evaluate(
            action_name=str(candidate["action_name"]),
            action_args=dict(candidate.get("action_args") or {}),
            prediction=dict(candidate.get("prediction") or {}),
            agency=self._pre_action_agency,
            capability_catalog=self._pre_action_capability_catalog,
            assurance_accepted=candidate.get("assurance_accepted") is True,
            authority_accepted=candidate.get("authority_accepted") is True,
            evaluation_phase="pre_action",
        )
        if (
            selected.action_name != str(name)
            or not selected.decision_fingerprint
            or selected.decision_fingerprint != expected.decision_fingerprint
        ):
            raise RuntimeError("Attractor selected-decision fingerprint mismatch")
        self._pending_attractor_evaluation = selected

    def _assert_point_of_effect_authorized(self, name: str) -> None:
        """Re-check non-model owner state immediately before crossing the boundary.

        The earlier owner stage may consume an exact one-time approval. This second
        check deliberately does not consume approval again; it re-evaluates kill,
        revocation and lease state after any durable intent preparation.
        """

        self.owner_control.assert_runtime_allowed()
        state = self.owner_control.snapshot()
        if bool(state.get("delegation_revoked")):
            raise OwnerControlError(
                f"external delegation was revoked before point of effect: {name}"
            )
        if bool(state.get("external_lease_required")) and not bool(
            state.get("lease_active")
        ):
            raise OwnerControlError(
                f"external delegation lease expired before point of effect: {name}"
            )

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
        try:
            self._assert_point_of_effect_authorized(name)
        except OwnerControlError as exc:
            return ToolResult(False, name, data={"owner_controlled": True}, error=str(exc))
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
            if name in EXTERNAL_IO_ACTIONS:
                try:
                    self._assert_point_of_effect_authorized(name)
                except OwnerControlError as exc:
                    return ToolResult(
                        False,
                        name,
                        data={"owner_controlled": True},
                        error=str(exc),
                    )
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
            self._assert_point_of_effect_authorized(name)
            result = next_action(name, args)
        except OwnerControlError as exc:
            closed = self.external_effects.record_result(
                intent.effect_id,
                ok=False,
                result={"owner_controlled": True, "error": str(exc)},
                no_effect_proven=True,
            )
            return ToolResult(
                False,
                name,
                data={
                    "owner_controlled": True,
                    "external_effect_id": intent.effect_id,
                    "external_effect_status": closed.status,
                    "suppressed": True,
                },
                error=str(exc),
            )
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

    def _safe_decision_after_rejection(
        self,
        original: Decision,
        assurance: dict[str, Any],
    ) -> Decision:
        self._pending_assurance_accepted = False
        return super()._safe_decision_after_rejection(original, assurance)

    def _record_forecast(self, decision: Any) -> int:
        self._pending_attractor_candidate = {
            "action_name": str(getattr(decision, "action_name", "")),
            "action_args": dict(getattr(decision, "action_args", {}) or {}),
            "prediction": dict(getattr(decision, "prediction", {}) or {}),
            # The rejection branch above explicitly sets False. Reaching this hook
            # without that branch means CriticAssurance accepted the selected decision.
            "assurance_accepted": self._pending_assurance_accepted is not False,
        }
        return super()._record_forecast(decision)

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
                capabilities = components.get("capabilities")
                catalog = (
                    capabilities.get("catalog")
                    if isinstance(capabilities, dict)
                    else {}
                )
                self._pre_action_agency = agency_before.as_dict()
                self._pre_action_capability_catalog = (
                    dict(catalog) if isinstance(catalog, dict) else {}
                )
                self._pending_attractor_candidate = None
                self._pending_attractor_evaluation = None
                self._pending_assurance_accepted = None
                report = super().cycle()
                evaluation = self._pending_attractor_evaluation
                if evaluation is None:
                    raise RuntimeError(
                        "selected action reached cycle completion without pre-action evaluation"
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
