from __future__ import annotations

from typing import Any

from .brain import Decision
from .executive import ExecutiveController, ExecutivePlan, ExecutivePolicy, ExecutiveStore
from .metabolic_runtime import MetabolicOrganismRuntime


class ExecutiveOrganismRuntime(MetabolicOrganismRuntime):
    """Genesis 1.3 runtime: deterministic executive control above Genesis 1.2 physiology.

    The Executive runs before inference. It may suppress model loading entirely, or
    constrain the configured model's token/thinking envelope for one cycle. It never
    grants capabilities and never fabricates verified resources. Concrete actions are
    still proposed by the replaceable cognitive substrate and reviewed by assurance.
    """

    EXECUTIVE_HISTORY_LIMIT = 8

    def __init__(
        self,
        *args: Any,
        executive_policy: ExecutivePolicy | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.executive = ExecutiveController(executive_policy)
        self.executive_store = ExecutiveStore(self.config.runtime.state_dir / "memory.sqlite3")
        self._current_executive_plan: ExecutivePlan | None = None
        self._current_executive_row_id: int | None = None

    def _context(self) -> dict[str, Any]:
        context = super()._context()
        plan = self.executive.plan(context)
        self._current_executive_plan = plan
        self._current_executive_row_id = self.executive_store.record(plan, context)
        context["executive"] = plan.as_dict()
        context["executive_history"] = self.executive_store.recent(self.EXECUTIVE_HISTORY_LIMIT)
        context["_system_prompt"] = self.prompt_template.render(context)
        return context

    @staticmethod
    def _no_brain_decision(plan: ExecutivePlan) -> Decision:
        return Decision(
            objective=f"Executive {plan.mode}: {plan.focus.name}",
            summary=(
                "Deterministic Executive suppressed expensive cognition for this cycle. "
                + " ".join(plan.reasons)
            )[:4000],
            action_name="noop",
            prediction={
                "action_success_probability": 0.999,
                "expected_outcome": "No external side effect; preserve current state and wake policy.",
                "expected_information_gain": 0.0,
                "expected_value": 0.0,
                "unit": "VALUE_UNIT",
            },
            sleep_seconds=plan.sleep_seconds,
        )

    def _think(self, context: dict[str, Any]) -> Decision:
        plan = self._current_executive_plan or self.executive.plan(context)
        if not plan.cognitive_budget.wake_brain:
            return self._no_brain_decision(plan)

        brain_config = self.config.brain
        original_max_tokens = int(brain_config.max_tokens)
        original_thinking = bool(brain_config.thinking)
        budget = plan.cognitive_budget
        try:
            if budget.max_tokens > 0:
                brain_config.max_tokens = min(original_max_tokens, int(budget.max_tokens))
            # Deep reasoning is an Executive decision. Explicitly configured thinking
            # remains allowed, while the Executive may also elevate a deep tier when
            # its policy permits adaptive thinking.
            brain_config.thinking = bool(original_thinking or budget.allow_thinking)
            return super()._think(context)
        finally:
            brain_config.max_tokens = original_max_tokens
            brain_config.thinking = original_thinking

    def cycle(self) -> dict[str, Any]:
        self._current_executive_plan = None
        self._current_executive_row_id = None
        brain_seconds_before = self.memory.brain_seconds_this_week()
        try:
            report = super().cycle()
        except BaseException as exc:
            row_id = self._current_executive_row_id
            if row_id is not None:
                brain_seconds_after = self.memory.brain_seconds_this_week()
                self.executive_store.resolve(
                    row_id,
                    brain_seconds_used=max(0.0, brain_seconds_after - brain_seconds_before),
                    action_name="<cycle_exception>",
                    result_ok=False,
                    outcome={"error": f"{type(exc).__name__}: {str(exc)[:2000]}"},
                )
            raise

        plan = self._current_executive_plan
        row_id = self._current_executive_row_id
        brain_seconds_after = self.memory.brain_seconds_this_week()
        brain_seconds_used = max(0.0, brain_seconds_after - brain_seconds_before)
        if row_id is not None:
            result = report.get("result") or {}
            decision = report.get("decision") or {}
            self.executive_store.resolve(
                row_id,
                brain_seconds_used=brain_seconds_used,
                action_name=str(decision.get("action_name", "")),
                result_ok=bool(result.get("ok")),
                outcome={
                    "chronicle_seq": report.get("chronicle_seq"),
                    "next_wake_at": report.get("next_wake_at"),
                    "homeostasis_mode": (report.get("homeostasis") or {}).get("mode"),
                },
            )
        if plan is not None:
            report["executive"] = {
                **plan.as_dict(),
                "record_id": row_id,
                "brain_seconds_used": brain_seconds_used,
            }
        return report


EliaExecutiveRuntime = ExecutiveOrganismRuntime
