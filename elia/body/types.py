from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BodyCapability:
    name: str
    description: str
    args: str
    authority: str
    side_effects: str
    network_scope: str
    cost_class: str
    enabled: bool
    readiness: str = "ready"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BodyResult:
    ok: bool
    capability: str
    data: Any = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
