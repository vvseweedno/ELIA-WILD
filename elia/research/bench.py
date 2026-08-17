from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Iterable

from .decay import DecaySchedule
from .memory import FractalMemory, ScrollMemory, build_memory_backend
from .stress import generation_stability, multiset_pattern_retention, needle_score


@dataclass(frozen=True, slots=True)
class MemoryBackendAblation:
    backend: str
    final_state: Any
    finite: bool
    impulse_retention: float
    mean_abs_output: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_real(value: Any) -> float:
    if isinstance(value, complex):
        return float(abs(value))
    return float(value)


def run_memory_backend_ablation(
    values: Iterable[float] | None = None,
    *,
    gate: float = 0.9,
) -> list[MemoryBackendAblation]:
    """Deterministic reference ablation, not a language-model benchmark."""

    sequence = list(values) if values is not None else [1.0] + [0.0] * 31
    results: list[MemoryBackendAblation] = []
    for name in ("scroll", "fractal", "lru_scan", "holo_scan"):
        if name == "scroll":
            backend = ScrollMemory(capacity=128)
        elif name == "fractal":
            backend = FractalMemory(
                levels=4,
                threshold=0.0,
                decay=DecaySchedule("silver"),
            )
        else:
            backend = build_memory_backend(name)
        outputs: list[float] = []
        for value in sequence:
            if name == "fractal":
                output = backend.step(value, gate=1.0)  # type: ignore[arg-type]
            else:
                output = backend.step(value, gate=gate)
            outputs.append(_as_real(output))
        final = backend.state()
        finite = all(isfinite(item) for item in outputs)
        impulse = outputs[-1] if outputs else 0.0
        mean_abs = sum(abs(item) for item in outputs) / len(outputs) if outputs else 0.0
        results.append(
            MemoryBackendAblation(
                backend=name,
                final_state=final,
                finite=finite,
                impulse_retention=impulse,
                mean_abs_output=mean_abs,
            )
        )
    return results


def run_reference_cognitive_stress() -> dict[str, Any]:
    needle = "ELIA continuity anchor 7f3c"
    context = ("noise token " * 2048) + needle + (" tail noise" * 2048)
    generations = [
        "preserve continuity evidence memory goals uncertainty",
        "preserve memory continuity goals evidence uncertainty",
        "continuity evidence preserves memory goals with uncertainty",
    ]
    reference = "alpha beta gamma delta epsilon"
    scrambled = "epsilon gamma alpha delta beta"
    return {
        "needle": needle_score(context, needle),
        "generation": generation_stability(generations),
        "scrambled_pattern": multiset_pattern_retention(reference, scrambled),
        "memory_backends": [item.as_dict() for item in run_memory_backend_ablation()],
    }
