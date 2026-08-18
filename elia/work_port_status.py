from __future__ import annotations

from copy import deepcopy
from typing import Any

from .config import Config
from .work_ports import WorkPortRegistry


def public_work_port_state(value: dict[str, Any]) -> dict[str, Any]:
    """Evidence-minimized work-port projection safe for status/MCP/model coordination."""

    ports: dict[str, Any] = {}
    for name, raw in dict(value.get("ports") or {}).items():
        if not isinstance(raw, dict):
            continue
        ports[str(name)[:128]] = {
            key: deepcopy(raw.get(key))
            for key in ("server", "submit_tool", "outcome_tool")
            if key in raw
        }
    submissions: list[dict[str, Any]] = []
    for raw in list(value.get("active_submissions") or [])[:32]:
        if not isinstance(raw, dict):
            continue
        submissions.append(
            {
                key: deepcopy(raw.get(key))
                for key in (
                    "id",
                    "work_item_id",
                    "port_name",
                    "submitted_at",
                    "updated_at",
                    "submission_observation_id",
                    "remote_status",
                    "last_outcome_observation_id",
                )
                if key in raw
            }
        )
    return {
        "enabled": bool(value.get("enabled", False)),
        "readiness": deepcopy(value.get("readiness")),
        "ports": ports,
        "active_submissions": submissions,
        "authority_rule": (
            "configured port name fixes MCP server/tool authority; external submission reference stays private"
        ),
    }


def work_port_status(config: Config) -> dict[str, Any]:
    registry = WorkPortRegistry(
        config.runtime.state_dir / "workspace",
        config.raw_tools,
    )
    return public_work_port_state(registry.diagnostics())
