from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .cognitive_energy import CognitiveEnergyController
from .config import Config
from .executive import ExecutiveController, ExecutivePolicy, ExecutiveStore


def executive_status(
    config: Config,
    *,
    resources: dict[str, Any],
    needs: list[dict[str, Any]],
    active_goals: list[dict[str, Any]],
    chronicle_valid: bool,
    chronicle_error: str | None,
    identity_drift: dict[str, Any] | None = None,
    history_limit: int = 8,
) -> dict[str, Any]:
    """Project current deterministic Executive state without invoking the model.

    This helper is shared by CLI and MCP so external introspection cannot drift into a
    different arbitration implementation from the production Executive runtime.
    """

    configured = asdict(config.executive)
    enabled = bool(configured.pop("enabled", True))
    policy = ExecutivePolicy(**configured)
    store = ExecutiveStore(config.runtime.state_dir / "memory.sqlite3")
    history = store.recent(max(3, min(int(history_limit), 32)))
    energy_controller = CognitiveEnergyController(policy)
    energy = energy_controller.summarize(history)
    if not enabled:
        return {
            "enabled": False,
            "plan": None,
            "energy": energy.as_dict(),
            "recent": history,
        }

    context = {
        "resources": resources,
        "needs": needs,
        "active_goals": active_goals,
        "chronicle_integrity": {
            "valid": bool(chronicle_valid),
            "error": chronicle_error,
        },
        "identity_drift": identity_drift or {"status": "unknown"},
    }
    plan = energy_controller.constrain(ExecutiveController(policy).plan(context), energy)
    return {
        "enabled": True,
        "plan": plan.as_dict(),
        "energy": energy.as_dict(),
        "recent": history,
    }
