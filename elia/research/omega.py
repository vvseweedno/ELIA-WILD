from __future__ import annotations

from dataclasses import dataclass
from math import tanh
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class OmegaConfig:
    context_anchor_strength: float = 0.1
    film_bound: float = 0.25
    omega_filter_alpha: float = 0.8
    recurrent_cycles: int = 3


def context_anchor(hidden: Any, anchor: Any, *, strength: float = 0.1) -> Any:
    """Reference ContextAnchor operation from the Elia Omega line."""
    return hidden + anchor * float(strength)


def bounded_depth_film(
    value: Any,
    *,
    gamma: Any,
    beta: Any,
    depth_signal: float,
    bound: float = 0.25,
) -> Any:
    """Bounded feature-wise modulation conditioned on recurrent/depth state.

    The scalar depth gate is bounded with tanh so repeated cycles cannot grow the
    modulation coefficient without limit.
    """

    gate = max(-abs(float(bound)), min(abs(float(bound)), tanh(float(depth_signal)) * abs(float(bound))))
    return value * (1.0 + gamma * gate) + beta * gate


@dataclass(slots=True)
class OmegaFilter:
    """Small adaptive exponential filter retained as a weak-positive Omega ablation."""

    alpha: float = 0.8
    _state: float | None = None

    def step(self, value: float) -> float:
        alpha = max(0.0, min(1.0, float(self.alpha)))
        value = float(value)
        if self._state is None:
            self._state = value
        else:
            self._state = alpha * self._state + (1.0 - alpha) * value
        return self._state

    @property
    def state(self) -> float | None:
        return self._state


@dataclass(slots=True)
class TriCore:
    """Shared-weight recurrent multi-cycle wrapper.

    `core` is the same callable on every cycle. The wrapper records intermediate
    states so auxiliary cycle supervision can be tested as an ablation rather than
    silently assumed beneficial.
    """

    core: Callable[[Any, int], Any]
    cycles: int = 3

    def run(self, state: Any) -> tuple[Any, list[Any]]:
        history: list[Any] = []
        current = state
        for cycle in range(max(1, int(self.cycles))):
            current = self.core(current, cycle)
            history.append(current)
        return current, history


def cycle_supervision_loss(
    losses: list[Any],
    *,
    final_weight: float = 1.0,
    intermediate_weight: float = 0.0,
) -> Any:
    """Explicit auxiliary-cycle loss ablation.

    Default intermediate_weight=0 encodes the historical fact that auxiliary cycle
    supervision remains unproven rather than making it an invisible default.
    """

    if not losses:
        raise ValueError("at least one cycle loss is required")
    result = losses[-1] * float(final_weight)
    if intermediate_weight and len(losses) > 1:
        for item in losses[:-1]:
            result = result + item * float(intermediate_weight) / (len(losses) - 1)
    return result
