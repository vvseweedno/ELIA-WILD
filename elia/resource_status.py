from __future__ import annotations

from typing import Any

from .config import Config
from .resource_ecology import ResourceEcologyEngine


def resource_ecology_status(
    config: Config,
    *,
    metabolism_snapshot: dict[str, Any],
    limit: int = 16,
) -> dict[str, Any]:
    """Return the deterministic read-only resource-ecology projection.

    Shared by CLI/MCP so external introspection uses the same exact `(asset, unit)`
    alignment rules as the production runtime. This function never invokes the model
    and cannot mutate verified resource state.
    """

    engine = ResourceEcologyEngine(config.runtime.state_dir / "memory.sqlite3")
    return engine.snapshot(
        metabolism_snapshot,
        limit=max(1, min(int(limit), 64)),
    )
