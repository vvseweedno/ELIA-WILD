from __future__ import annotations

from pathlib import Path

from elia.brain import Decision
from elia.config import BrainConfig, Config, RuntimeConfig
from elia.executive import ExecutiveController, ExecutivePolicy, ExecutiveStore
from elia.executive_runtime import ExecutiveOrganismRuntime


def _base_context() -> dict:
    return {
        "resources": {"weekly_limit_hours": 30.0, "runtime_hours_remaining": 30.0},
        "needs": [],
        "active_goals": [],
        "chronicle_integrity": {"valid": True, "error": None},
        "identity_drift": {"status": "stable"},
    }


def _config(tmp_path: Path, *, weekly_hours: float = 30.0, max_tokens: int = 2048) -> Config:
    root = Path(__file__).resolve().parents[1]
    return Config(
        identity_name="ELIA",
        identity_statement="Executive integration test seed.",
        mission=["preserve continuity", "act on verified priorities"],
        brain=BrainConfig(
            backend="mock",
            model_id="mock",
            base_url="http://127.0.0.1:8000/v1",
            timeout_seconds=5,
            max_tokens=max_tokens,
            temperature=0,
            top_p=1,
            thinking=False,
        ),
        runtime=RuntimeConfig(
            state_dir=tmp_path / ".elia",
            cycle_sleep_seconds=0,
            max_action_output_chars=16000,
            weekly_gpu_budget_hours=weekly_hours,
            memory_recall_limit=12,
        ),
        raw_tools={"http_get": {"enabled": False}, "body": {}},
        subject_core_path=root / "config" / "subject_core.yaml",
        continuity_constitution_path=root / "config" / "continuity_constitution.yaml",
        system_prompt_path=root / "config" / "system_prompt.md",
        skills_dir=root / "skills",
    )


class ExplodingBrain:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, context: dict) -> Decision:
        self.calls += 1
        raise AssertionError("Executive should have suppressed expensive cognition")


class BudgetCaptureBrain:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.observed: list[tuple[int, bool, dict]] = []

    def decide(self, context: dict) -> Decision:
        self.observed.append(
            (
                self.config.brain.max_tokens,
                self.config.brain.thinking,
                dict(context.get("executive") or {}),
            )
        )
        return Decision(
            objective="Protect continuity before optional work.",
            summary="Respond to the deterministic Executive focus.",
            action_name="noop",
            prediction={
                "action_success_probability": 0.99,
                "expected_outcome": "No external side effect.",
                "expected_information_gain": 0,
                "expected_value": 0,
                "unit": "VALUE_UNIT",
            },
            sleep_seconds=0,
        )


def test_executive_halts_on_critical_identity_drift() -> None:
    context = _base_context()
    context["identity_drift"] = {"status": "critical"}
    plan = ExecutiveController().plan(context)
    assert plan.mode == "halt"
    assert plan.interrupt is True
    assert plan.cognitive_budget.wake_brain is False
    assert plan.cognitive_budget.tier == "none"


def test_executive_selects_highest_active_goal_when_maintenance_is_quiet() -> None:
    context = _base_context()
    context["active_goals"] = [
        {"id": 1, "title": "Blocked high goal", "priority": 1.0, "status": "blocked"},
        {"id": 2, "title": "Executable mission", "priority": 0.90, "status": "active"},
        {"id": 3, "title": "Low mission", "priority": 0.30, "status": "active"},
    ]
    plan = ExecutiveController().plan(context)
    assert plan.mode == "mission"
    assert plan.focus.kind == "goal"
    assert plan.focus.id == 2
    assert plan.focus.name == "Executable mission"
    assert plan.cognitive_budget.wake_brain is True


def test_low_compute_runway_forces_low_cognitive_tier() -> None:
    context = _base_context()
    context["resources"]["runtime_hours_remaining"] = 2.0
    context["needs"] = [
        {
            "name": "resource_acquisition",
            "severity": 0.78,
            "reason": "Verified compute runway is low.",
            "response_hint": "Prefer cheap resource-extending work.",
        }
    ]
    plan = ExecutiveController().plan(context)
    assert plan.mode == "resource"
    assert plan.cognitive_budget.tier == "low"
    assert plan.cognitive_budget.max_tokens == 256
    assert plan.cognitive_budget.allow_thinking is False


def test_executive_store_records_and_resolves_measured_cycle(tmp_path: Path) -> None:
    context = _base_context()
    context["needs"] = [
        {
            "name": "runtime_reliability",
            "severity": 0.9,
            "reason": "Recent deterministic failures require diagnosis.",
            "response_hint": "Diagnose before expansion.",
        }
    ]
    plan = ExecutiveController().plan(context)
    store = ExecutiveStore(tmp_path / "memory.sqlite3")
    row_id = store.record(plan, context)
    store.resolve(
        row_id,
        brain_seconds_used=3.5,
        action_name="self_check",
        result_ok=True,
        outcome={"homeostasis_mode": "strained"},
    )
    item = store.recent(1)[0]
    assert item["id"] == row_id
    assert item["brain_seconds_used"] == 3.5
    assert item["action_name"] == "self_check"
    assert item["result_ok"] == 1


def test_runtime_exhausted_budget_never_calls_brain(tmp_path: Path) -> None:
    config = _config(tmp_path, weekly_hours=0.0)
    brain = ExplodingBrain()
    runtime = ExecutiveOrganismRuntime(config, brain=brain)
    report = runtime.cycle()

    assert brain.calls == 0
    assert report["executive"]["mode"] == "hibernate"
    assert report["executive"]["cognitive_budget"]["wake_brain"] is False
    assert report["decision"]["action_name"] == "noop"
    assert report["executive"]["brain_seconds_used"] == 0.0


def test_runtime_applies_cycle_local_token_and_thinking_budget_then_restores(tmp_path: Path) -> None:
    config = _config(tmp_path, weekly_hours=30.0, max_tokens=2048)
    brain = BudgetCaptureBrain(config)
    runtime = ExecutiveOrganismRuntime(
        config,
        brain=brain,
        executive_policy=ExecutivePolicy(deep_focus_threshold=0.80),
    )
    report = runtime.cycle()

    assert brain.observed
    observed_tokens, observed_thinking, executive = brain.observed[0]
    # First boot has a deterministic durable-checkpoint pressure at severity 0.85.
    assert executive["mode"] == "maintenance"
    assert executive["cognitive_budget"]["tier"] == "deep"
    assert observed_tokens == 1024
    assert observed_thinking is True
    assert config.brain.max_tokens == 2048
    assert config.brain.thinking is False
    assert report["executive"]["record_id"] is not None
