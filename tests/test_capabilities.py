from __future__ import annotations

from pathlib import Path

from elia.autonomy import derive_needs
from elia.brain import Decision
from elia.config import BrainConfig, Config, RuntimeConfig
from elia.memory import MemoryStore
from elia.runtime import EliaRuntime
from elia.tools import ToolRegistry


def make_config(tmp_path: Path) -> Config:
    return Config(
        identity_name="ELIA",
        identity_statement="Capability-health test seed.",
        mission=["preserve continuity", "repair from verified evidence"],
        brain=BrainConfig(
            backend="mock",
            model_id="mock",
            base_url="http://127.0.0.1:8000/v1",
            timeout_seconds=5,
            max_tokens=128,
            temperature=0.7,
            top_p=0.8,
            thinking=False,
        ),
        runtime=RuntimeConfig(
            state_dir=tmp_path / ".elia",
            cycle_sleep_seconds=0,
            max_action_output_chars=16000,
            weekly_gpu_budget_hours=30,
            memory_recall_limit=12,
        ),
        raw_tools={"http_get": {"enabled": True}},
    )


class RepeatedFailureBrain:
    def decide(self, context):
        return Decision(
            objective="Try the same missing workspace read.",
            summary="Synthetic repeated failure for degradation testing.",
            action_name="read_workspace",
            action_args={"path": "missing.txt"},
            sleep_seconds=0,
        )


def test_capability_catalog_is_structured(tmp_path: Path) -> None:
    registry = ToolRegistry(tmp_path / "workspace")
    catalog = registry.catalog()
    assert catalog["http_get"]["authority"] == "public_network_read"
    assert catalog["http_get"]["network_scope"] == "public_http_https"
    assert catalog["self_check"]["authority"] == "local_self_diagnostic"
    assert catalog["propose_repair"]["side_effects"].startswith("writes a proposal")


def test_disabled_capability_is_visible_and_rejected(tmp_path: Path) -> None:
    registry = ToolRegistry(tmp_path / "workspace", {"http_get": {"enabled": False}})
    assert registry.catalog()["http_get"]["enabled"] is False
    result = registry.execute("http_get", {"url": "https://example.com"})
    assert result.ok is False
    assert "disabled" in result.error


def test_self_check_cleans_up_scratch_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    registry = ToolRegistry(workspace)
    result = registry.execute("self_check")
    assert result.ok is True
    assert all(result.data["checks"].values())
    assert list(workspace.glob(".selfcheck-*")) == []


def test_repair_proposal_is_staged_not_deployed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    registry = ToolRegistry(workspace)
    result = registry.execute(
        "propose_repair",
        {
            "title": "Repair a degraded parser",
            "diagnosis": "Three verified parsing failures occurred.",
            "proposed_change": "Change the parser fallback order in a future reviewed patch.",
            "validation_plan": "Run parser regression tests and a three-cycle smoke test.",
        },
    )
    assert result.ok is True
    assert result.data["status"] == "proposal_only"
    proposal = workspace / result.data["path"]
    assert proposal.is_file()
    assert proposal.is_relative_to(workspace.resolve())
    assert not (tmp_path / "elia").exists()


def test_capability_health_tracks_consecutive_failures(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    memory.record_capability_event("read_workspace", ok=False, error="one")
    memory.record_capability_event("read_workspace", ok=False, error="two")
    memory.record_capability_event("read_workspace", ok=False, error="three")
    health = memory.capability_health("read_workspace")
    assert health["attempts"] == 3
    assert health["failures"] == 3
    assert health["consecutive_failures"] == 3
    assert memory.capability_degraded("read_workspace") is True


def test_runtime_suppresses_blind_retry_after_three_failures(tmp_path: Path) -> None:
    runtime = EliaRuntime(make_config(tmp_path), brain=RepeatedFailureBrain())
    first = runtime.cycle()
    second = runtime.cycle()
    third = runtime.cycle()
    fourth = runtime.cycle()

    assert first["result"]["ok"] is False
    assert second["result"]["ok"] is False
    assert third["result"]["ok"] is False
    assert fourth["result"]["ok"] is False
    assert fourth["result"]["data"]["suppressed"] is True

    health = runtime.memory.capability_health("read_workspace")
    assert health["attempts"] == 3
    assert health["suppressed"] == 1
    assert health["consecutive_failures"] == 3

    budget = runtime.budget()
    needs = derive_needs(
        runtime.memory,
        chronicle_valid=True,
        budget=budget,
        active_goals=runtime.memory.active_goals(),
        capability_health=runtime.capability_state()["health"],
    )
    assert "capability_repair" in {need.name for need in needs}


def test_success_resets_consecutive_failure_streak(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    for _ in range(3):
        memory.record_capability_event("write_workspace", ok=False, error="synthetic")
    assert memory.capability_degraded("write_workspace") is True
    memory.record_capability_event("write_workspace", ok=True)
    health = memory.capability_health("write_workspace")
    assert health["consecutive_failures"] == 0
    assert memory.capability_degraded("write_workspace") is False
