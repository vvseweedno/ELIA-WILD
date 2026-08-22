from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Iterable


SILVER_RATIO_CONJUGATE = sqrt(2.0) - 1.0


@dataclass(frozen=True, slots=True)
class DecaySchedule:
    """Reference *retention* schedule used by recurrent-memory ablations.

    ``coefficient(step)`` is the one-step retention coefficient. Use
    ``attenuation(depth)`` when a signal must monotonically decay through repeated
    depth; applying the same coefficient independently at every layer is not decay.
    """

    kind: str = "silver"
    rho: float | None = None

    def coefficient(self, step: int = 0, *, learned_rho: float | None = None) -> float:
        step = max(0, int(step))
        if self.kind == "silver":
            return SILVER_RATIO_CONJUGATE
        if self.kind == "half":
            return 0.5
        if self.kind == "learned":
            value = learned_rho if learned_rho is not None else self.rho
            if value is None:
                raise ValueError("learned decay requires rho")
            value = float(value)
            if not isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("rho must be finite and within [0, 1]")
            return value
        if self.kind == "octagonal":
            # Historical HoloSeraphim multi-rate schedule:
            # [sr², sr², sr, sr, 1-sr, 1-sr, 1, 1]
            sr = SILVER_RATIO_CONJUGATE
            values = (sr * sr, sr * sr, sr, sr, 1.0 - sr, 1.0 - sr, 1.0, 1.0)
            return values[step % len(values)]
        raise ValueError(f"unknown decay schedule: {self.kind}")

    def sequence(self, length: int, *, learned_rho: float | None = None) -> list[float]:
        return [self.coefficient(step, learned_rho=learned_rho) for step in range(max(0, int(length)))]

    def attenuation(self, depth: int, *, learned_rho: float | None = None) -> float:
        depth = int(depth)
        if depth < 0:
            raise ValueError("depth must be non-negative")
        result = 1.0
        for step in range(depth + 1):
            result *= self.coefficient(step, learned_rho=learned_rho)
        if not isfinite(result):
            raise ValueError("cumulative decay attenuation must be finite")
        return max(0.0, min(1.0, result))


def apply_decay(values: Iterable[float], schedule: DecaySchedule) -> list[float]:
    result: list[float] = []
    attenuation = 1.0
    for index, raw in enumerate(values):
        value = float(raw)
        if not isfinite(value):
            raise ValueError("decay values must be finite")
        attenuation *= schedule.coefficient(index)
        if not isfinite(attenuation):
            raise ValueError("cumulative decay attenuation must be finite")
        result.append(value * attenuation)
    return result
