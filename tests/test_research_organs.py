from __future__ import annotations

import math

import pytest

from elia.research.decay import DecaySchedule, SILVER_RATIO_CONJUGATE, apply_decay
from elia.research.memory import (
    AffineRecurrence,
    FractalMemory,
    ScrollMemory,
    build_memory_backend,
    holo_scan,
    lru_scan,
)
from elia.research.omega import OmegaFilter, TriCore, bounded_depth_film, context_anchor
from elia.research.registry import RESEARCH_REGISTRY, maturity_summary
from elia.research.seraphim import ouroboros_inject, surprisal_from_token_loss
from elia.research.stress import (
    generation_stability,
    multiset_pattern_retention,
    needle_score,
    token_jaccard,
)


def test_decay_schedules_preserve_canonical_ablation_options() -> None:
    assert math.isclose(DecaySchedule("silver").coefficient(), SILVER_RATIO_CONJUGATE)
    assert DecaySchedule("half").coefficient() == 0.5
    assert DecaySchedule("learned", rho=0.7).coefficient() == 0.7
    octagonal = DecaySchedule("octagonal").sequence(8)
    assert len(octagonal) == 8
    assert octagonal[-2:] == [1.0, 1.0]
    assert octagonal[0] == octagonal[1]


def test_affine_recurrence_composition_is_associative() -> None:
    a = AffineRecurrence(0.8, 1.0)
    b = AffineRecurrence(0.7, 2.0)
    c = AffineRecurrence(0.6, 3.0)
    left = a.then(b).then(c)
    right = a.then(b.then(c))
    assert abs(left.a - right.a) < 1e-12
    assert abs(left.b - right.b) < 1e-12


def test_lru_scan_matches_direct_recurrence() -> None:
    values = [1.0, 2.0, 3.0]
    gates = [0.5, 0.5, 0.5]
    scanned = lru_scan(values, gates)
    direct = []
    state = 0.0
    for value, gate in zip(values, gates):
        state = gate * state + (1.0 - gate) * value
        direct.append(state)
    assert scanned == direct


def test_holo_scan_preserves_complex_state() -> None:
    result = holo_scan([1 + 1j, 2 - 1j], [0.5, 0.5], phases=[0.1, -0.2])
    assert len(result) == 2
    assert all(isinstance(item, complex) for item in result)


def test_fractal_memory_uses_surprisal_gated_writes() -> None:
    memory = FractalMemory(levels=3, threshold=0.5)
    before = memory.state()
    memory.step(10.0, surprisal=0.1)
    assert memory.state()["writes"] == before["writes"]
    memory.step(10.0, surprisal=0.9)
    assert memory.state()["writes"] == before["writes"] + 1
    assert memory.read() != 0.0


def test_memory_backend_registry_exposes_all_canonical_backends() -> None:
    assert build_memory_backend("scroll").name == "scroll"
    assert build_memory_backend("fractal").name == "fractal"
    assert build_memory_backend("lru_scan").name == "lru_scan"
    assert build_memory_backend("holo_scan").name == "holo_scan"


def test_ouroboros_reference_uses_explicit_decay() -> None:
    injected = ouroboros_inject(1.0, 2.0, depth=0, schedule=DecaySchedule("half"), strength=0.5)
    assert injected == 1.5
    assert 0.0 < surprisal_from_token_loss(1.0) < 1.0


def test_decay_is_cumulative_and_monotone_through_depth() -> None:
    schedule = DecaySchedule("half")
    assert [schedule.attenuation(depth) for depth in range(4)] == [0.5, 0.25, 0.125, 0.0625]
    assert apply_decay([1.0] * 4, schedule) == [0.5, 0.25, 0.125, 0.0625]
    assert ouroboros_inject(0.0, 1.0, depth=3, schedule=schedule) == 0.0625


@pytest.mark.parametrize("invalid", [-1.0, float("nan"), float("inf")])
def test_surprisal_gate_rejects_invalid_token_nll(invalid: float) -> None:
    with pytest.raises(ValueError, match="token NLL"):
        surprisal_from_token_loss(invalid)


def test_recurrent_scan_rejects_silent_zip_truncation() -> None:
    with pytest.raises(ValueError, match="equal length"):
        lru_scan([1.0, 2.0], [0.5])


def test_memory_reference_backends_reject_nonfinite_state_and_invalid_gates() -> None:
    with pytest.raises(ValueError, match="scroll memory value"):
        ScrollMemory().step(float("nan"))
    with pytest.raises(ValueError, match="initial scan state"):
        lru_scan([1.0], [0.5], initial=float("inf"))
    with pytest.raises(ValueError, match=r"within \[0, 1\]"):
        lru_scan([1.0], [1.1])
    with pytest.raises(ValueError, match="state length"):
        FractalMemory(levels=2, _level_state=[0.0])


@pytest.mark.parametrize("backend", [ScrollMemory(), FractalMemory()])
def test_real_memory_backends_reject_nonreal_complex_values(backend) -> None:
    with pytest.raises(ValueError, match="real-valued"):
        backend.step(1.0 + 1.0j)


def test_learned_decay_rejects_out_of_range_retention() -> None:
    with pytest.raises(ValueError, match=r"within \[0, 1\]"):
        DecaySchedule("learned", rho=1.1).coefficient()


def test_omega_reference_components_are_bounded_and_shared_weight() -> None:
    assert context_anchor(1.0, 2.0, strength=0.25) == 1.5
    modulated = bounded_depth_film(1.0, gamma=10.0, beta=0.0, depth_signal=100, bound=0.1)
    assert modulated <= 2.0

    filt = OmegaFilter(alpha=0.5)
    assert filt.step(1.0) == 1.0
    assert filt.step(3.0) == 2.0

    calls = []
    core = TriCore(lambda state, cycle: calls.append(cycle) or state + 1, cycles=3)
    final, history = core.run(0)
    assert final == 3
    assert history == [1, 2, 3]
    assert calls == [0, 1, 2]


def test_stress_metrics_are_diagnostics_not_persona_identity() -> None:
    assert needle_score("alpha hidden key omega", "hidden key") == 1.0
    assert multiset_pattern_retention("a b c", "c a b") == 1.0
    assert 0 <= token_jaccard("same idea", "same different") <= 1
    stability = generation_stability(["alpha beta", "alpha gamma"])
    assert "mean_pairwise_jaccard" in stability


def test_research_registry_keeps_archived_and_prototype_separate() -> None:
    summary = maturity_summary()
    assert "holo_complex_model" in summary["archived"]
    assert "lru_scan" in summary["prototype"]
    assert RESEARCH_REGISTRY["context_anchor"].default_runtime is False
