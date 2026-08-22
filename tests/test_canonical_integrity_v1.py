from __future__ import annotations

import math
from pathlib import Path

import pytest

from elia.attractor import AutonomyAttractor
from elia.canonical import canonical_json
from elia.organism_runtime import _safe_action_descriptor
from elia.causal import CausalMemoryStore
from elia.executive import ExecutiveStore
from elia.organism import OrganismManifest
from elia.state_bus import OrganismStateBus
from elia.world_model import WorldModelStore


class StringMasquerade:
    def __str__(self) -> str:
        return "same-as-a-literal"


@pytest.mark.parametrize(
    "value, error",
    [
        (StringMasquerade(), TypeError),
        (float("nan"), ValueError),
        (float("inf"), ValueError),
        ({1: "integer key collides with string key"}, TypeError),
        ({"nested": {"value"}}, TypeError),
    ],
)
def test_canonical_json_rejects_lossy_or_nonfinite_values(value, error) -> None:
    with pytest.raises(error):
        canonical_json(value)


def test_canonical_json_is_order_stable_and_rejects_cycles() -> None:
    assert canonical_json({"b": 2, "a": [1, True]}) == canonical_json(
        {"a": [1, True], "b": 2}
    )
    cycle: list[object] = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="circular"):
        canonical_json(cycle)


def test_action_descriptor_rejects_lossy_fingerprint_inputs() -> None:
    class Stringifiable:
        def __str__(self) -> str:
            return "same-as-a-real-string"

    with pytest.raises(TypeError, match="non-JSON value"):
        _safe_action_descriptor("noop", {"value": Stringifiable()})
    with pytest.raises(ValueError, match="non-finite"):
        _safe_action_descriptor("noop", {"value": float("nan")})


def test_integrity_stores_fail_before_persisting_masquerading_values(
    tmp_path: Path,
) -> None:
    causal = CausalMemoryStore(tmp_path / "causal.sqlite3")
    with pytest.raises(TypeError, match="non-JSON value"):
        causal.record_intervention(
            action_name="test",
            arguments={"value": StringMasquerade()},
            outcome={"ok": True},
            success=True,
            duration_ms=1.0,
        )
    assert causal.recent(10) == []

    bus = OrganismStateBus(tmp_path / "bus.sqlite3")
    transaction = bus.begin("strict payload")
    with pytest.raises(ValueError, match="non-finite"):
        bus.append(
            transaction,
            phase="action",
            kind="INVALID",
            payload={"value": math.nan},
        )
    assert [event.kind for event in bus.events(transaction)] == ["TRANSACTION_BEGIN"]

    world = WorldModelStore(tmp_path / "world.sqlite3")
    with pytest.raises(TypeError, match="non-JSON value"):
        world.propose(
            domain="test",
            subject="subject",
            predicate="predicate",
            object=StringMasquerade(),
            confidence=0.5,
            evidence="test",
        )
    assert world.snapshot()["beliefs"] == []


def test_executive_and_attractor_hash_boundaries_reject_invalid_values(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        ExecutiveStore.context_digest({"public": math.nan})

    contract = tmp_path / "attractor.md"
    contract.write_text("strict test contract", encoding="utf-8")
    attractor = AutonomyAttractor.load(contract)
    with pytest.raises(TypeError, match="non-JSON value"):
        attractor.evaluate(
            action_name="noop",
            action_args={"value": StringMasquerade()},
            prediction={},
            agency={},
            capability_catalog={
                "noop": {
                    "enabled": True,
                    "authority": "none",
                    "side_effects": "none",
                    "cost_class": "low",
                }
            },
            assurance_accepted=True,
            authority_accepted=True,
            evaluation_phase="pre_action",
        )


def test_manifest_fingerprint_rejects_yaml_implicit_datetime(tmp_path: Path) -> None:
    manifest_path = tmp_path / "organism.yaml"
    manifest_path.write_text(
        """
schema_version: 1
identity_id: strict-test
name: Strict Test
created_at: 2026-08-22
organs:
  - id: test
    layer: core
    kind: python
    module: elia.canonical
    maturity: core
    required: true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    manifest = OrganismManifest.load(manifest_path)
    with pytest.raises(TypeError, match="datetime.date"):
        _ = manifest.fingerprint
