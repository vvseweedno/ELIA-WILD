from __future__ import annotations

from dataclasses import asdict, dataclass, field
import importlib
import os
import platform
import sys
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class RuntimeCheck:
    name: str
    ok: bool
    required: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuntimeCompatibilityChecker:
    """Smoke-first dependency/runtime checker extracted from archived TPU/Kaggle failures."""

    def check(
        self,
        *,
        required_modules: tuple[str, ...] = (),
        optional_modules: tuple[str, ...] = (),
        required_env: tuple[str, ...] = (),
        require_cuda: bool = False,
    ) -> list[RuntimeCheck]:
        checks: list[RuntimeCheck] = [
            RuntimeCheck("python>=3.11", sys.version_info >= (3, 11), True, sys.version.split()[0]),
            RuntimeCheck("platform", True, False, platform.platform()),
        ]
        for module_name in required_modules:
            try:
                module = importlib.import_module(module_name)
                version = getattr(module, "__version__", "unknown")
                checks.append(RuntimeCheck(module_name, True, True, str(version)))
            except Exception as exc:
                checks.append(
                    RuntimeCheck(
                        module_name,
                        False,
                        True,
                        f"{type(exc).__name__}: {str(exc)[:500]}",
                    )
                )
        for module_name in optional_modules:
            try:
                module = importlib.import_module(module_name)
                version = getattr(module, "__version__", "unknown")
                checks.append(RuntimeCheck(module_name, True, False, str(version)))
            except Exception as exc:
                checks.append(
                    RuntimeCheck(
                        module_name,
                        False,
                        False,
                        f"{type(exc).__name__}: {str(exc)[:500]}",
                    )
                )
        for name in required_env:
            present = bool(os.getenv(name, "").strip())
            checks.append(RuntimeCheck(f"env:{name}", present, True, "set" if present else "missing"))
        if require_cuda:
            try:
                import torch

                available = bool(torch.cuda.is_available())
                detail = (
                    torch.cuda.get_device_name(0)
                    if available and torch.cuda.device_count() > 0
                    else "CUDA unavailable"
                )
            except Exception as exc:
                available = False
                detail = f"{type(exc).__name__}: {str(exc)[:500]}"
            checks.append(RuntimeCheck("cuda", available, True, detail))
        return checks

    @staticmethod
    def healthy(checks: list[RuntimeCheck]) -> bool:
        return all(item.ok for item in checks if item.required)


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    name: str
    source: str
    split: str = "train"
    weight: float = 1.0
    gated: bool = False
    auth_env: str | None = None
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DatasetCocktailRegistry:
    """Validated dataset plan; it does not download data or bypass gating/auth."""

    datasets: dict[str, DatasetSpec] = field(default_factory=dict)

    def register(self, spec: DatasetSpec) -> None:
        if not spec.name.strip() or not spec.source.strip():
            raise ValueError("dataset name and source are required")
        if spec.weight <= 0:
            raise ValueError("dataset weight must be positive")
        if spec.name in self.datasets:
            raise ValueError(f"duplicate dataset: {spec.name}")
        self.datasets[spec.name] = spec

    def validate(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        ready = True
        for name, spec in sorted(self.datasets.items()):
            auth_ok = True
            if spec.gated:
                auth_ok = bool(spec.auth_env and os.getenv(spec.auth_env, "").strip())
            if not auth_ok:
                ready = False
            items.append({**spec.as_dict(), "auth_ready": auth_ok})
        return {"ready": ready, "datasets": items}


@dataclass(frozen=True, slots=True)
class SmokeResult:
    passed: bool
    smoke_output: Any
    full_output: Any | None


class SmokeFirstRunner:
    """Never enter the expensive path unless the cheap smoke path succeeds."""

    def run(
        self,
        smoke: Callable[[], Any],
        full: Callable[[], Any],
        *,
        predicate: Callable[[Any], bool] | None = None,
    ) -> SmokeResult:
        smoke_output = smoke()
        passed = bool(predicate(smoke_output) if predicate else smoke_output)
        if not passed:
            return SmokeResult(False, smoke_output, None)
        return SmokeResult(True, smoke_output, full())
