from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from typing import Any

from . import __version__
from .homeostasis import HomeostasisEngine
from .runtime import EliaRuntime as GenesisRuntime


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _safe_action_descriptor(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Describe an action for durable logs without persisting argument values."""
    return {
        "name": str(name),
        "argument_keys": sorted(str(key) for key in args)[:64],
        "arguments_fingerprint": _fingerprint(args),
    }


class OrganismRuntime(GenesisRuntime):
    """Genesis 1.1 runtime with sensorimotor, world and homeostatic integration.

    The proven Genesis runtime remains the stable base. This layer makes lived
    external experience part of every future cognitive context without depending on
    the model to remember to write an autobiographical memory after an action.

    Raw body/action arguments are deliberately excluded from Chronicle and ordinary
    autobiographical records. Full normalized outcomes live in the private Sensorium;
    durable high-level logs keep fingerprints and provenance pointers instead.
    """

    WORLD_CONTEXT_LIMIT = 24
    SENSORIUM_CONTEXT_LIMIT = 8
    CAUSAL_CONTEXT_LIMIT = 12

    def _boot(self) -> None:
        super()._boot()
        recovered = self.tools.state_bus.reconcile_incomplete(
            "reconciled during organism boot after an interrupted prior process"
        )
        if recovered:
            self.memory.remember(
                "runtime_recovery",
                f"Reconciled {recovered} incomplete organism transaction(s) after boot.",
                importance=0.9,
                source="organism_runtime",
                metadata={"reconciled_transactions": recovered},
            )
            self.chronicle.append(
                "STATE_BUS_RECOVERY",
                {"reconciled_transactions": recovered},
            )

    def _homeostasis_snapshot(self) -> dict[str, Any]:
        active_tx = getattr(self, "_active_cycle_transaction_id", None)
        ignored = {active_tx} if active_tx else set()
        return HomeostasisEngine(
            self.config.runtime.state_dir,
            self.tools.observations,
            self.tools.world_model,
            self.tools.state_bus,
            self.tools.body.diagnostics(),
        ).evaluate(ignore_transaction_ids=ignored).as_dict()

    def _state_components(self) -> dict[str, Any]:
        components = super()._state_components()
        components["world_model"] = self.tools.world_model.snapshot(self.WORLD_CONTEXT_LIMIT)
        components["sensorium"] = self.tools.observations.snapshot(self.SENSORIUM_CONTEXT_LIMIT)
        components["causal_memory"] = self.tools.causal.snapshot(self.CAUSAL_CONTEXT_LIMIT)
        components["digital_body"] = self.tools.body.diagnostics()
        incomplete = self.tools.state_bus.incomplete(32)
        components["state_bus"] = {
            "incomplete_count": len(incomplete),
            "incomplete": incomplete[:8],
        }

        homeostasis = self._homeostasis_snapshot()
        components["homeostasis"] = homeostasis
        needs = list(components.get("needs") or [])
        names = {str(item.get("name", "")) for item in needs if isinstance(item, dict)}
        for signal in homeostasis.get("signals", []):
            if not isinstance(signal, dict):
                continue
            name = str(signal.get("name", ""))
            if not name or name in names:
                continue
            needs.append(
                {
                    "name": name,
                    "severity": float(signal.get("severity", 0.0)),
                    "reason": str(signal.get("reason", "")),
                    "response_hint": str(signal.get("response_hint", "")),
                    "source": "homeostasis",
                    "evidence": signal.get("evidence") or {},
                }
            )
            names.add(name)
        needs.sort(key=lambda item: (-float(item.get("severity", 0.0)), str(item.get("name", ""))))
        components["needs"] = needs[:16]
        self_model = components.get("self_model")
        if isinstance(self_model, dict):
            self_model["needs"] = [str(item.get("name", "")) for item in components["needs"]]
            self_model["homeostasis_mode"] = homeostasis.get("mode")
        return components

    def _memory_queries(self, components: dict[str, Any]) -> list[str]:
        queries = list(super()._memory_queries(components))
        for belief in components.get("world_model", {}).get("beliefs", [])[:12]:
            queries.extend(
                [
                    str(belief.get("subject", "")),
                    str(belief.get("predicate", "")),
                    str(belief.get("object", "")),
                    str(belief.get("evidence", "")),
                ]
            )
        for observation in components.get("sensorium", [])[:6]:
            queries.extend(
                [
                    str(observation.get("source_ref", "")),
                    str(observation.get("summary", "")),
                ]
            )
        for signal in components.get("homeostasis", {}).get("signals", [])[:8]:
            queries.extend(
                [str(signal.get("name", "")), str(signal.get("reason", ""))]
            )
        unique: list[str] = []
        seen: set[str] = set()
        for item in queries:
            cleaned = str(item).strip()
            folded = cleaned.casefold()
            if cleaned and folded not in seen:
                unique.append(cleaned)
                seen.add(folded)
        return unique[:96]

    def _context(self) -> dict[str, Any]:
        context = super()._context()
        # The base context deliberately stays backward-compatible. The organism
        # layer adds bounded snapshots directly from durable state and re-renders
        # the project-owned cognitive contract so these organs are available before
        # the model chooses its next action.
        context["world_model"] = self.tools.world_model.snapshot(self.WORLD_CONTEXT_LIMIT)
        context["sensorium"] = self.tools.observations.snapshot(self.SENSORIUM_CONTEXT_LIMIT)
        context["causal_memory"] = self.tools.causal.snapshot(self.CAUSAL_CONTEXT_LIMIT)
        context["digital_body"] = self.tools.body.diagnostics()
        context["homeostasis"] = self._homeostasis_snapshot()
        incomplete = self.tools.state_bus.incomplete(16)
        context["organism_state_bus"] = {
            "incomplete_count": len(incomplete),
            "incomplete": incomplete[:4],
        }
        context["_system_prompt"] = self.prompt_template.render(context)
        return context

    def _latest_action_observation(self, action_name: str) -> dict[str, Any] | None:
        items = self.tools.observations.recent(1)
        if not items or items[0].source_ref != action_name:
            return None
        item = items[0]
        return {
            "id": item.id,
            "transaction_id": item.transaction_id,
            "payload_sha256": item.payload_sha256,
            "source_kind": item.source_kind,
            "success": item.success,
            "summary": item.summary[:1000],
        }

    def cycle(self) -> dict[str, Any]:
        cycle_tx = self.tools.state_bus.begin(
            "cognitive_cycle",
            identity_fingerprint=self.identity.fingerprint,
        )
        self._active_cycle_transaction_id = cycle_tx
        homeostasis_at_wake = self._homeostasis_snapshot()
        self.tools.state_bus.append(
            cycle_tx,
            phase="perception",
            kind="COGNITIVE_WAKE",
            payload={
                "world_belief_count": len(self.tools.world_model.snapshot(256)["beliefs"]),
                "recent_observation_count": len(self.tools.observations.snapshot(64)),
                "homeostasis_mode": homeostasis_at_wake.get("mode"),
                "brain_loaded": self.brain_loaded,
            },
        )

        try:
            context = self._context()
            proposed = self._think(context)
            assurance_report = self.assurance.review(proposed, context).as_dict()
            decision = (
                proposed
                if assurance_report["accepted"]
                else self._safe_decision_after_rejection(proposed, assurance_report)
            )

            memory_ids = (
                self._store_model_memories(decision) if assurance_report["accepted"] else []
            )
            self_changes = (
                self._apply_self_updates(decision) if assurance_report["accepted"] else []
            )
            goal_changes = (
                self._apply_goal_updates(decision) if assurance_report["accepted"] else []
            )
            opportunity_changes = (
                self._apply_opportunity_updates(decision)
                if assurance_report["accepted"]
                else []
            )

            # Forecast is committed BEFORE the intervention.
            forecast_id = self._record_forecast(decision)
            result = self._execute_action(decision.action_name, decision.action_args)
            result_full = result.as_dict()
            brier_score = self.metacognition.resolve(
                forecast_id,
                success=result.ok,
                observation=result_full,
            )
            calibration = self.metacognition.calibration(100)

            result_dict = result_full
            max_chars = self.config.runtime.max_action_output_chars
            serialized = json.dumps(result_dict, ensure_ascii=False, sort_keys=True, default=str)
            if len(serialized) > max_chars:
                result_dict = {
                    "ok": result.ok,
                    "tool": result.tool,
                    "data": {"truncated_result": serialized[:max_chars]},
                    "error": result.error,
                }

            sleep_seconds, next_wake_at = self._schedule_next_wake(decision.sleep_seconds)
            self_model_id, self_model_fingerprint, post_components = self._record_self_model(
                source="cycle"
            )

            observation_ref = self._latest_action_observation(decision.action_name)
            safe_result = {
                "ok": result.ok,
                "tool": result.tool,
                "error": (str(result.error)[:1000] if result.error else None),
                "observation": observation_ref,
            }
            action_record = {
                "identity_fingerprint": self.identity.fingerprint,
                "prompt_fingerprint": self.prompt_template.fingerprint,
                "body_version": __version__,
                "objective": decision.objective,
                "summary": decision.summary,
                "skill": decision.skill_name,
                "proposed": {
                    "objective": proposed.objective,
                    "summary": proposed.summary,
                    "skill": proposed.skill_name,
                    "prediction": proposed.prediction,
                    "action": _safe_action_descriptor(
                        proposed.action_name,
                        proposed.action_args,
                    ),
                },
                "assurance": assurance_report,
                "forecast": {
                    "id": forecast_id,
                    "prediction": decision.prediction,
                    "brier_score": brier_score,
                    "calibration": calibration,
                },
                "action": _safe_action_descriptor(
                    decision.action_name,
                    decision.action_args,
                ),
                "result": safe_result,
                "memory_ids": memory_ids,
                "self_changes": self_changes,
                "goal_changes": goal_changes,
                "opportunity_changes": opportunity_changes,
                "capability_health": self.memory.capability_health(decision.action_name),
                "homeostasis": {
                    "mode": post_components.get("homeostasis", {}).get("mode"),
                    "signals": [
                        {
                            "name": item.get("name"),
                            "severity": item.get("severity"),
                        }
                        for item in post_components.get("homeostasis", {}).get("signals", [])[:8]
                    ],
                },
                "self_model": {
                    "row_id": self_model_id,
                    "fingerprint": self_model_fingerprint,
                    "drift": post_components["drift"],
                },
                "scheduler": {
                    "sleep_seconds": sleep_seconds,
                    "next_wake_at": next_wake_at,
                },
            }
            self.memory.set_meta(
                "last_action",
                json.dumps(action_record, ensure_ascii=False, sort_keys=True),
            )
            self.memory.remember(
                "action_result",
                json.dumps(action_record, ensure_ascii=False, sort_keys=True)[:16000],
                importance=0.6 if result.ok and assurance_report["accepted"] else 0.85,
                source="organism_runtime",
                metadata={
                    "identity_fingerprint": self.identity.fingerprint,
                    "self_model_fingerprint": self_model_fingerprint,
                    "forecast_id": forecast_id,
                    "brier_score": brier_score,
                    "observation_id": (
                        observation_ref.get("id") if observation_ref is not None else None
                    ),
                    "observation_sha256": (
                        observation_ref.get("payload_sha256")
                        if observation_ref is not None
                        else None
                    ),
                },
            )
            entry = self.chronicle.append("CYCLE", action_record)
            self._account_runtime()

            report = {
                "chronicle_seq": entry.seq,
                "identity_fingerprint": self.identity.fingerprint,
                "self_model_fingerprint": self_model_fingerprint,
                "decision": {
                    "objective": decision.objective,
                    "summary": decision.summary,
                    "skill": decision.skill_name,
                    "action_name": decision.action_name,
                },
                "assurance": assurance_report,
                "forecast": {
                    "id": forecast_id,
                    "prediction": decision.prediction,
                    "brier_score": brier_score,
                    "calibration": calibration,
                },
                "result": result_dict,
                "observation": observation_ref,
                "self_changes": self_changes,
                "goal_changes": goal_changes,
                "opportunity_changes": opportunity_changes,
                "active_goals": [asdict(goal) for goal in self.memory.active_goals(16)],
                "self_hypotheses": self.self_hypotheses.snapshot(24),
                "economy": self.economy.snapshot(16),
                "capability_health": self.memory.capability_health(decision.action_name),
                "identity_drift": post_components["drift"],
                "homeostasis": post_components.get("homeostasis", {}),
                "resources": self.budget(),
                "sleep_seconds": sleep_seconds,
                "next_wake_at": next_wake_at,
            }

            self.tools.state_bus.append(
                cycle_tx,
                phase="learning",
                kind="COGNITIVE_OUTCOME",
                payload={
                    "chronicle_seq": entry.seq,
                    "action_name": decision.action_name,
                    "action_arguments_fingerprint": _fingerprint(decision.action_args),
                    "result_ok": result.ok,
                    "observation_id": (
                        observation_ref.get("id") if observation_ref is not None else None
                    ),
                    "observation_sha256": (
                        observation_ref.get("payload_sha256")
                        if observation_ref is not None
                        else None
                    ),
                    "homeostasis_mode": post_components.get("homeostasis", {}).get("mode"),
                    "self_model_fingerprint": self_model_fingerprint,
                    "next_wake_at": next_wake_at,
                },
            )
            self.tools.state_bus.commit(
                cycle_tx,
                {
                    "chronicle_seq": entry.seq,
                    "next_wake_at": next_wake_at,
                },
            )
            report["organism_transaction_id"] = cycle_tx
            report["world_model"] = self.tools.world_model.snapshot(12)
            report["causal_memory"] = self.tools.causal.snapshot(8)
            return report

        except BaseException as exc:
            # Preserve the original exception while attempting to close the
            # write-ahead transaction as explicitly aborted.
            try:
                self.tools.state_bus.abort(
                    cycle_tx,
                    f"{type(exc).__name__}: {str(exc)[:2000]}",
                )
            except Exception:
                pass
            raise
        finally:
            self._active_cycle_transaction_id = None


# Explicit alias for callers that want the latest organism runtime while the older
# EliaRuntime class remains import-compatible for regression and migration tooling.
EliaOrganismRuntime = OrganismRuntime
