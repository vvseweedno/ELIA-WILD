from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Config
from .epistemic import CognitiveBiographyStore, EpistemicCortex, EpistemicRegistry
from .provider_context import provider_context


def epistemic_status(config: Config) -> dict[str, Any]:
    """Return the public read-only epistemic ecosystem projection.

    Private session transcripts/context views remain local SQLite state. This helper is
    shared by CLI and MCP so external introspection cannot drift from provider privacy.
    """
    registry = EpistemicRegistry.load(Path(config.epistemic_path))
    store = CognitiveBiographyStore(config.runtime.state_dir / "memory.sqlite3")
    snapshot = EpistemicCortex(registry, store).snapshot()
    projected = provider_context({"epistemic_ecosystem": snapshot}).get(
        "epistemic_ecosystem", {}
    )
    return projected if isinstance(projected, dict) else {}
