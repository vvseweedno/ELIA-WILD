from __future__ import annotations

from copy import deepcopy
from typing import Any

from .config import Config
from .resource_ingress import ResourceIngressRegistry


def public_resource_ingress(value: dict[str, Any]) -> dict[str, Any]:
    """Return a verifier state projection that never exposes signing-key metadata or raw event IDs."""

    verifiers: dict[str, Any] = {}
    for name, raw in dict(value.get("verifiers") or {}).items():
        if not isinstance(raw, dict):
            continue
        verifiers[str(name)[:128]] = {
            key: deepcopy(raw.get(key))
            for key in ("server", "tool", "authority", "asset", "unit", "kind", "key_present")
            if key in raw
        }
    recent: list[dict[str, Any]] = []
    for raw in list(value.get("recent") or [])[:32]:
        if not isinstance(raw, dict):
            continue
        recent.append(
            {
                key: deepcopy(raw.get(key))
                for key in (
                    "id",
                    "verifier_name",
                    "external_event_sha256",
                    "status",
                    "asset",
                    "unit",
                    "amount",
                    "observation_id",
                    "resource_event_id",
                    "work_item_id",
                )
                if key in raw
            }
        )
    return {
        "enabled": bool(value.get("enabled", False)),
        "readiness": deepcopy(value.get("readiness")),
        "verifiers": verifiers,
        "recent": recent,
        "authority_rule": (
            "configured verifier fixes MCP source and resource type; signing key, external_event_id and raw evidence stay local"
        ),
        "replay_rule": (
            "same verifier/external event is idempotent; conflicting replay fails closed"
        ),
    }


def resource_ingress_status(config: Config) -> dict[str, Any]:
    registry = ResourceIngressRegistry(config.runtime.state_dir, config.raw_tools)
    return public_resource_ingress(registry.diagnostics())
