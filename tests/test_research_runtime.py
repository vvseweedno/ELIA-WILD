from __future__ import annotations

from elia.research.bench import run_memory_backend_ablation, run_reference_cognitive_stress
from elia.research.cache import StatefulMemoryCache
from elia.research.runtime import (
    DatasetCocktailRegistry,
    DatasetSpec,
    RuntimeCompatibilityChecker,
    SmokeFirstRunner,
)


def test_stateful_memory_cache_is_stream_scoped_and_resettable() -> None:
    cache = StatefulMemoryCache("lru_scan")
    a1 = cache.step("a", 1.0, gate=0.5)
    b1 = cache.step("b", 0.0, gate=0.5)
    assert a1 != b1
    assert cache.snapshot()["stream_count"] == 2
    cache.reset("a")
    assert cache.snapshot()["stream_count"] == 1


def test_runtime_checker_distinguishes_required_and_optional() -> None:
    checker = RuntimeCompatibilityChecker()
    checks = checker.check(required_modules=("json",), optional_modules=("definitely_missing_elia_module",))
    assert checker.healthy(checks) is True
    assert any(item.name == "definitely_missing_elia_module" and not item.ok and not item.required for item in checks)


def test_dataset_registry_does_not_bypass_gating(monkeypatch) -> None:
    monkeypatch.delenv("ELIA_DATASET_TOKEN", raising=False)
    registry = DatasetCocktailRegistry()
    registry.register(
        DatasetSpec(
            name="gated",
            source="example/gated",
            gated=True,
            auth_env="ELIA_DATASET_TOKEN",
        )
    )
    assert registry.validate()["ready"] is False
    monkeypatch.setenv("ELIA_DATASET_TOKEN", "present")
    assert registry.validate()["ready"] is True


def test_smoke_first_runner_never_enters_full_path_after_failed_smoke() -> None:
    calls: list[str] = []
    result = SmokeFirstRunner().run(
        lambda: False,
        lambda: calls.append("full"),
    )
    assert result.passed is False
    assert result.full_output is None
    assert calls == []


def test_memory_backend_ablation_exercises_all_reference_backends() -> None:
    results = run_memory_backend_ablation()
    assert {item.backend for item in results} == {"scroll", "fractal", "lru_scan", "holo_scan"}
    assert all(item.finite for item in results)
    stress = run_reference_cognitive_stress()
    assert stress["needle"] == 1.0
    assert stress["scrambled_pattern"] == 1.0
