from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Iterable


SILVER_RATIO_CONJUGATE = sqrt(2.0) - 1.0


@dataclass(frozen=True, slots=True)
class DecaySchedule:
    """Reference decay schedule used by Ouroboros and recurrent-memory ablations."""

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
            if not isfinite(value):
                raise ValueError("rho must be finite")
            return max(0.0, min(0.999999, value))
        if self.kind == "octagonal":
            # Historical HoloSeraphim multi-rate schedule:
            # [sr², sr², sr, sr, 1-sr, 1-sr, 1, 1]
            sr = SILVER_RATIO_CONJUGATE
            values = (sr * sr, sr * sr, sr, sr, 1.0 - sr, 1.0 - sr, 1.0, 1.0)
            return values[step % len(values)]
        raise ValueError(f"unknown decay schedule: {self.kind}")

    def sequence(self, length: int, *, learned_rho: float | None = None) -> list[float]:
        return [self.coefficient(step, learned_rho=learned_rho) for step in range(max(0, int(length)))]


def apply_decay(values: Iterable[float], schedule: DecaySchedule) -> list[float]:
    return [float(value) * schedule.coefficient(index) for index, value in enumerate(values)]
