from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from .autonomy import derive_needs
from .chronicle import Chronicle
from .cognitive_energy import CognitiveEnergyController
from .config import Config
from .economy import EconomyStore
from .executive import ExecutiveController, ExecutivePolicy, ExecutiveStore
from .homeostasis import HomeostasisEngine
from .memory import MemoryStore
from .metabolism import MetabolismEngine
from .resource_status import resource_ecology_needs, resource_ecology_status
from .tools import ToolRegistry


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


def executive_status_from_state(
    config: Config,
    *,
    memory: MemoryStore | None = None,
    tools: ToolRegistry | None = None,
    history_limit: int = 8,
) -> dict[str, Any]:
    """Build the same Executive projection directly from persistent organism state."""

    state_dir = config.runtime.state_dir
    database = state_dir / "memory.sqlite3"
    memory = memory or MemoryStore(database)
    tools = tools or ToolRegistry(state_dir / "workspace", config.raw_tools)
    economy = EconomyStore(database).snapshot(16)
    metabolism = MetabolismEngine(
        database,
        weekly_gpu_budget_hours=config.runtime.weekly_gpu_budget_hours,
    ).snapshot().as_dict()
    ecology = resource_ecology_status(config, metabolism_snapshot=metabolism, limit=16)
    homeostasis = HomeostasisEngine(
        state_dir,
        tools.observations,
        tools.world_model,
        tools.state_bus,
        tools.body.diagnostics(),
        metabolism_snapshot=metabolism,
    ).evaluate().as_dict()

    limit = config.runtime.weekly_gpu_budget_hours
    runtime_hours = memory.runtime_seconds_this_week() / 3600.0
    resources = {
        "weekly_limit_hours": limit,
        "runtime_hours_used": runtime_hours,
        "brain_hours_used": memory.brain_seconds_this_week() / 3600.0,
        "runtime_hours_remaining": max(0.0, limit - runtime_hours),
    }
    active_goals = memory.active_goals(16)
    catalog = tools.catalog()
    capability_health = memory.capability_health_all(list(catalog), window=20)
    chronicle_valid, chronicle_error = Chronicle(state_dir / "chronicle.jsonl").verify()
    needs = [
        item.as_dict()
        for item in derive_needs(
            memory,
            chronicle_valid=chronicle_valid,
            budget=resources,
            active_goals=active_goals,
            capability_health=capability_health,
            economy=economy,
        )
    ]
    names = {str(item.get("name", "")) for item in needs}
    for signal in homeostasis.get("signals", []):
        if not isinstance(signal, dict):
            continue
        name = str(signal.get("name", ""))
        if not name or name in names:
            continue
        needs.append(
            {
                "name": name,
                "severity": float(signal.get("severity", 0.0)),
                "reason": str(signal.get("reason", "")),
                "response_hint": str(signal.get("response_hint", "")),
                "source": "homeostasis",
                "evidence": signal.get("evidence") or {},
            }
        )
        names.add(name)
    for item in resource_ecology_needs(ecology):
        name = str(item.get("name", ""))
        if name and name not in names:
            needs.append(item)
            names.add(name)
    needs.sort(key=lambda item: (-float(item.get("severity", 0.0)), str(item.get("name", ""))))

    drift_raw = memory.get_meta("last_drift_report", "") or ""
    try:
        drift = json.loads(drift_raw) if drift_raw else {"status": "unknown"}
    except json.JSONDecodeError:
        drift = {"status": "unknown", "error": "invalid stored drift report"}
    if not isinstance(drift, dict):
        drift = {"status": "unknown"}

    result = executive_status(
        config,
        resources=resources,
        needs=needs[:20],
        active_goals=[asdict(goal) for goal in active_goals],
        chronicle_valid=chronicle_valid,
        chronicle_error=chronicle_error,
        identity_drift=drift,
        history_limit=history_limit,
    )
    result["inputs"] = {
        "resources": resources,
        "needs": needs[:20],
        "active_goal_count": len(active_goals),
        "homeostasis_mode": homeostasis.get("mode"),
        "resource_ecology": {
            "exact_bottleneck_candidate_count": ecology.get(
                "exact_bottleneck_candidate_count", 0
            ),
            "active_work_count": len(ecology.get("active_work") or []),
            "bottleneck": ecology.get("bottleneck"),
        },
    }
    return result
