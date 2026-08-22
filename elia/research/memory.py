from __future__ import annotations

from dataclasses import dataclass, field
from math import exp, isfinite
from typing import Any, Iterable, Protocol, Sequence

from .decay import DecaySchedule


class MemoryBackend(Protocol):
    name: str

    def step(
        self,
        value: float | complex,
        *,
        gate: float | None = None,
    ) -> float | complex: ...

    def state(self) -> Any: ...


@dataclass(slots=True)
class ScrollMemory:
    """Small bounded chronological memory reference backend."""

    capacity: int = 128
    name: str = "scroll"
    _items: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.capacity = int(self.capacity)
        if self.capacity < 1:
            raise ValueError("scroll memory capacity must be positive")
        normalized = [float(item) for item in self._items]
        if any(not isfinite(item) for item in normalized):
            raise ValueError("scroll memory state must be finite")
        self._items = normalized[-self.capacity :]

    def step(self, value: float | complex, *, gate: float | None = None) -> float:
        if isinstance(value, complex):
            if value.imag != 0.0:
                raise ValueError("scroll memory requires a real-valued input")
            value = value.real
        value = float(value)
        if not isfinite(value):
            raise ValueError("scroll memory value must be finite")
        self._items.append(value)
        if len(self._items) > self.capacity:
            del self._items[: len(self._items) - self.capacity]
        return value

    def state(self) -> list[float]:
        return list(self._items)


@dataclass(slots=True)
class FractalMemory:
    """Surprisal-gated multi-scale memory reference implementation.

    This is deliberately model-agnostic. It preserves the canonical research idea:
    writes are gated by surprisal and retained at progressively coarser scales.
    """

    levels: int = 4
    threshold: float = 0.5
    decay: DecaySchedule = field(default_factory=DecaySchedule)
    name: str = "fractal"
    _level_state: list[float] = field(default_factory=list)
    _writes: int = 0

    def __post_init__(self) -> None:
        self.levels = int(self.levels)
        if not 1 <= self.levels <= 64:
            raise ValueError("fractal memory levels must be in 1..64")
        if not isfinite(float(self.threshold)) or not 0.0 <= float(self.threshold) <= 1.0:
            raise ValueError("fractal memory threshold must be finite and within [0, 1]")
        if not self._level_state:
            self._level_state = [0.0 for _ in range(self.levels)]
        else:
            self._level_state = [float(item) for item in self._level_state]
            if len(self._level_state) != self.levels:
                raise ValueError("fractal memory state length must equal levels")
            if any(not isfinite(item) for item in self._level_state):
                raise ValueError("fractal memory state must be finite")
        if int(self._writes) < 0:
            raise ValueError("fractal memory write count must be non-negative")
        self._writes = int(self._writes)

    def step(
        self,
        value: float | complex,
        *,
        gate: float | None = None,
        surprisal: float | None = None,
    ) -> float:
        if isinstance(value, complex):
            if value.imag != 0.0:
                raise ValueError("fractal memory requires a real-valued input")
            value = value.real
        value = float(value)
        if not isfinite(value):
            raise ValueError("memory value must be finite")
        surprise = float(surprisal if surprisal is not None else (gate if gate is not None else 1.0))
        if not isfinite(surprise):
            raise ValueError("surprisal must be finite")
        if not 0.0 <= surprise <= 1.0:
            raise ValueError("bounded surprisal gate must be within [0, 1]")
        if surprise < self.threshold:
            return self.read()

        self._writes += 1
        carry = value
        for level in range(len(self._level_state)):
            rho = self.decay.coefficient(level)
            self._level_state[level] = rho * self._level_state[level] + (1.0 - rho) * carry
            carry = self._level_state[level]
            # Coarser levels update less frequently: 1, 1/2, 1/4, ...
            if self._writes % (2 ** (level + 1)) != 0:
                break
        return self.read()

    def read(self) -> float:
        weights = [1.0 / (2**index) for index in range(len(self._level_state))]
        denominator = sum(weights) or 1.0
        return sum(value * weight for value, weight in zip(self._level_state, weights)) / denominator

    def state(self) -> dict[str, Any]:
        return {"levels": list(self._level_state), "writes": self._writes, "read": self.read()}


@dataclass(frozen=True, slots=True)
class AffineRecurrence:
    """One recurrence element: h_out = a * h_in + b."""

    a: complex
    b: complex

    def apply(self, state: complex) -> complex:
        return self.a * state + self.b

    def then(self, later: "AffineRecurrence") -> "AffineRecurrence":
        """Compose self followed by later; associative by construction."""
        return AffineRecurrence(later.a * self.a, later.a * self.b + later.b)


def associative_prefix(elements: Sequence[AffineRecurrence]) -> list[AffineRecurrence]:
    """Reference prefix composition validating the associative scan algebra.

    The function is sequential for portability; the associative operator is what can
    be mapped to parallel scans in JAX/PyTorch research backends.
    """

    result: list[AffineRecurrence] = []
    current = AffineRecurrence(1.0 + 0j, 0.0 + 0j)
    for item in elements:
        current = current.then(item)
        result.append(current)
    return result


def lru_scan(
    values: Iterable[float],
    forget_gates: Iterable[float],
    *,
    initial: float = 0.0,
) -> list[float]:
    """Real-valued LRU-style recurrence reference.

    h_t = f_t * h_{t-1} + (1-f_t) * x_t
    """

    value_list = [float(value) for value in values]
    gate_list = [float(gate) for gate in forget_gates]
    if len(value_list) != len(gate_list):
        raise ValueError("values and forget_gates must have equal length")
    initial_value = float(initial)
    if not isfinite(initial_value):
        raise ValueError("initial scan state must be finite")
    elements: list[AffineRecurrence] = []
    for value, gate in zip(value_list, gate_list):
        if not isfinite(value) or not isfinite(gate):
            raise ValueError("scan values and gates must be finite")
        if not 0.0 <= gate <= 1.0:
            raise ValueError("forget gates must be within [0, 1]")
        f = gate
        elements.append(AffineRecurrence(complex(f), complex((1.0 - f) * value)))
    return [
        float(prefix.apply(complex(initial_value)).real)
        for prefix in associative_prefix(elements)
    ]


def log_domain_forget(raw: float) -> float:
    """Stable positive forget parameterization used by reference recurrent scans."""
    raw = float(raw)
    if not isfinite(raw):
        raise ValueError("raw forget parameter must be finite")
    raw = max(-60.0, min(60.0, raw))
    # sigmoid(-softplus(raw)) style bounded gate, represented without external deps.
    softplus = raw if raw > 20 else exp(raw) if raw < -20 else __import__("math").log1p(exp(raw))
    return exp(-softplus)


def holo_scan(
    values: Iterable[complex],
    forget_gates: Iterable[float],
    *,
    phases: Iterable[float] | None = None,
    initial: complex = 0j,
) -> list[complex]:
    """Minimal complex/phase recurrent scan retained as a Holo research backend.

    It preserves phase through a unit complex rotation while using the same associative
    affine recurrence. This is a reference experiment, not the archived full Holo model.
    """

    values_list = [complex(value) for value in values]
    gate_list = [float(gate) for gate in forget_gates]
    phase_list = [float(phase) for phase in phases] if phases is not None else [0.0] * len(values_list)
    if len(values_list) != len(gate_list) or len(values_list) != len(phase_list):
        raise ValueError("values, forget_gates and phases must have equal length")
    initial_value = complex(initial)
    if not isfinite(initial_value.real) or not isfinite(initial_value.imag):
        raise ValueError("initial Holo scan state must be finite")
    elements: list[AffineRecurrence] = []
    for value, gate, phase in zip(values_list, gate_list, phase_list):
        if not (
            isfinite(value.real)
            and isfinite(value.imag)
            and isfinite(gate)
            and isfinite(phase)
        ):
            raise ValueError("Holo scan values, gates and phases must be finite")
        if not 0.0 <= gate <= 1.0:
            raise ValueError("Holo forget gates must be within [0, 1]")
        f = gate
        rotation = complex(__import__("math").cos(phase), __import__("math").sin(phase))
        a = f * rotation
        b = (1.0 - f) * value
        elements.append(AffineRecurrence(a, b))
    return [prefix.apply(initial_value) for prefix in associative_prefix(elements)]


@dataclass(slots=True)
class RecurrentScanMemory:
    name: str = "lru_scan"
    complex_state: bool = False
    state_value: complex = 0j

    def __post_init__(self) -> None:
        self.state_value = complex(self.state_value)
        if not isfinite(self.state_value.real) or not isfinite(self.state_value.imag):
            raise ValueError("recurrent memory initial state must be finite")
        if not self.complex_state and self.state_value.imag != 0.0:
            raise ValueError("real recurrent memory initial state must be real")

    def step(self, value: float | complex, *, gate: float | None = None) -> float | complex:
        gate_value = 0.9 if gate is None else float(gate)
        if not isfinite(gate_value):
            raise ValueError("memory gate must be finite")
        if not 0.0 <= gate_value <= 1.0:
            raise ValueError("memory gate must be within [0, 1]")
        candidate = complex(value)
        if not isfinite(candidate.real) or not isfinite(candidate.imag):
            raise ValueError("memory value must be finite")
        f = gate_value
        if self.complex_state:
            self.state_value = f * self.state_value + (1.0 - f) * candidate
            return self.state_value
        if candidate.imag != 0.0:
            raise ValueError("real recurrent memory cannot accept a complex value")
        real = f * self.state_value.real + (1.0 - f) * candidate.real
        self.state_value = complex(real)
        return real

    def state(self) -> float | complex:
        return self.state_value if self.complex_state else self.state_value.real


def build_memory_backend(name: str, **kwargs: Any) -> MemoryBackend:
    normalized = str(name).strip().lower()
    if normalized == "scroll":
        return ScrollMemory(**kwargs)
    if normalized == "fractal":
        return FractalMemory(**kwargs)
    if normalized == "lru_scan":
        return RecurrentScanMemory(name="lru_scan", complex_state=False, **kwargs)
    if normalized == "holo_scan":
        return RecurrentScanMemory(name="holo_scan", complex_state=True, **kwargs)
    raise ValueError(f"unknown memory backend: {name}")
