from __future__ import annotations

from pathlib import Path

from elia.observations import ObservationStore
from elia.tools import ToolRegistry


def test_tool_execution_automatically_becomes_observation_and_experience(tmp_path: Path) -> None:
    registry = ToolRegistry(tmp_path / "workspace")
    result = registry.execute("write_workspace", {"path": "a.txt", "content": "hello"})
    assert result.ok is True

    observations = registry.observations.recent(8)
    assert observations[0].source_ref == "write_workspace"
    assert observations[0].success is True
    assert observations[0].transaction_id

    experiences = registry.causal.recent(8)
    assert experiences[0].action_name == "write_workspace"
    assert experiences[0].observation_id == observations[0].id
    valid, error = registry.state_bus.verify(observations[0].transaction_id or "")
    assert valid is True, error


def test_sensorium_payload_is_bounded_but_full_digest_is_stable(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path / "memory.sqlite3")
    huge = {"text": "x" * 700_000}
    observation = store.record(
        source_kind="test",
        source_ref="oversized",
        payload=huge,
        evidence if False else None,
    )
    assert observation.payload["_truncated"] is True
    assert observation.payload["original_sha256"] == observation.payload_sha256
    assert observation.payload["original_bytes"] > 512_000


def test_world_model_tools_never_self_verify(tmp_path: Path) -> None:
    registry = ToolRegistry(tmp_path / "workspace")
    created = registry.execute(
        "world_model_propose",
        {
            "domain": "test",
            "subject": "thing",
            "predicate": "state",
            "object": "present",
            "confidence": 1.0,
            "evidence": "observation says present",
        },
    )
    assert created.ok is True
    assert created.data["status"] == "hypothesis"
    assert created.data["confidence"] == 0.75

    rejected = registry.execute(
        "world_model_revise",
        {
            "id": created.data["id"],
            "status": "verified",
            "confidence": 1.0,
            "evidence": "model assertion",
        },
    )
    assert rejected.ok is False
    assert "hypothesis/supported/disputed" in (rejected.error or "")
