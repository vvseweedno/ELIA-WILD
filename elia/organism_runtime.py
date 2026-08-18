from __future__ import annotations

from typing import Any

from .runtime import EliaRuntime as GenesisRuntime


class OrganismRuntime(GenesisRuntime):
    """Genesis 1.1 runtime with automatic world/sensorimotor state integration.

    The proven Genesis runtime remains the stable base. This layer makes lived
    external experience part of every future cognitive context without depending on
    the model to remember to write an autobiographical memory after an action.
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
        incomplete = self.tools.state_bus.incomplete(16)
        context["organism_state_bus"] = {
            "incomplete_count": len(incomplete),
            "incomplete": incomplete[:4],
        }
        context["_system_prompt"] = self.prompt_template.render(context)
        return context

    def cycle(self) -> dict[str, Any]:
        cycle_tx = self.tools.state_bus.begin(
            "cognitive_cycle",
            identity_fingerprint=self.identity.fingerprint,
        )
        self.tools.state_bus.append(
            cycle_tx,
            phase="perception",
            kind="COGNITIVE_WAKE",
            payload={
                "world_belief_count": len(self.tools.world_model.snapshot(256)["beliefs"]),
                "recent_observation_count": len(self.tools.observations.snapshot(64)),
                "brain_loaded": self.brain_loaded,
            },
        )
        try:
            report = super().cycle()
        except BaseException as exc:
            # The state bus records the interrupted/failed cognitive transition.
            # We intentionally do not swallow the original exception.
            try:
                self.tools.state_bus.abort(
                    cycle_tx,
                    f"{type(exc).__name__}: {str(exc)[:2000]}",
                )
            except Exception:
                pass
            raise

        self.tools.state_bus.append(
            cycle_tx,
            phase="learning",
            kind="COGNITIVE_OUTCOME",
            payload={
                "chronicle_seq": report.get("chronicle_seq"),
                "action_name": (report.get("decision") or {}).get("action_name"),
                "result_ok": (report.get("result") or {}).get("ok"),
                "self_model_fingerprint": report.get("self_model_fingerprint"),
                "next_wake_at": report.get("next_wake_at"),
            },
        )
        self.tools.state_bus.commit(
            cycle_tx,
            {
                "chronicle_seq": report.get("chronicle_seq"),
                "next_wake_at": report.get("next_wake_at"),
            },
        )
        report["organism_transaction_id"] = cycle_tx
        report["world_model"] = self.tools.world_model.snapshot(12)
        report["causal_memory"] = self.tools.causal.snapshot(8)
        return report


# Explicit alias for callers that want the latest organism runtime while the older
# EliaRuntime class remains import-compatible for regression and migration tooling.
EliaOrganismRuntime = OrganismRuntime
