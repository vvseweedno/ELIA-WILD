from __future__ import annotations

import argparse
from dataclasses import asdict
import importlib.util
import ipaddress
import json
from pathlib import Path
from typing import Any

from . import __version__
from .chronicle import Chronicle
from .config import Config, load_config
from .homeostasis import HomeostasisEngine
from .identity import IdentityBundle, IdentityStore
from .lifecycle import evaluate_preflight
from .memory import MemoryStore
from .metabolism import MetabolismEngine
from .tools import ToolRegistry
from .vitals import VitalSigns


MAX_WORLD_QUERY = 32
MAX_SENSORIUM = 16


def _require_mcp() -> None:
    if importlib.util.find_spec("mcp") is None:
        raise RuntimeError(
            "ELIA MCP server requires the optional MCP v2 dependency; install with "
            "`pip install -e '.[mcp]'` or `pip install -e '.[sensorimotor]'`."
        )


def _safe_sensorium(tools: ToolRegistry, limit: int = 8) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in tools.observations.recent(max(1, min(int(limit), MAX_SENSORIUM))):
        result.append(
            {
                "id": item.id,
                "observed_at": item.observed_at,
                "transaction_id": item.transaction_id,
                "source_kind": item.source_kind,
                "source_ref": item.source_ref,
                "modality": item.modality,
                "content_type": item.content_type,
                "trust": item.trust,
                "success": item.success,
                "summary": item.summary[:1000],
                "payload_sha256": item.payload_sha256,
            }
        )
    return result


def _metabolism(config: Config) -> dict[str, Any]:
    return MetabolismEngine(
        config.runtime.state_dir / "memory.sqlite3",
        weekly_gpu_budget_hours=config.runtime.weekly_gpu_budget_hours,
    ).snapshot().as_dict()


def _homeostasis(config: Config, tools: ToolRegistry) -> dict[str, Any]:
    metabolism = _metabolism(config)
    return HomeostasisEngine(
        config.runtime.state_dir,
        tools.observations,
        tools.world_model,
        tools.state_bus,
        tools.body.diagnostics(),
        metabolism_snapshot=metabolism,
    ).evaluate().as_dict()


def _identity_snapshot(config: Config) -> dict[str, Any]:
    identity = IdentityBundle.load(
        config.subject_core_path,
        config.continuity_constitution_path,
    )
    store = IdentityStore(config.runtime.state_dir / "memory.sqlite3")
    lineage = store.last_lineage()
    return {
        "name": identity.name,
        "identity_id": identity.identity_id,
        "branch_id": config.branch_id,
        "identity_fingerprint": identity.fingerprint,
        "subject_core_fingerprint": identity.subject_core_fingerprint,
        "constitution_fingerprint": identity.constitution_fingerprint,
        "body_version": __version__,
        "lineage_head": asdict(lineage) if lineage is not None else None,
    }


def _status_snapshot(config: Config) -> dict[str, Any]:
    state_dir = config.runtime.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    memory = MemoryStore(state_dir / "memory.sqlite3")
    tools = ToolRegistry(state_dir / "workspace", config.raw_tools)
    identity = IdentityBundle.load(
        config.subject_core_path,
        config.continuity_constitution_path,
    )
    chronicle_valid, chronicle_error = Chronicle(state_dir / "chronicle.jsonl").verify()
    preflight = evaluate_preflight(
        state_dir,
        config.runtime.weekly_gpu_budget_hours,
        expected_identity_fingerprint=identity.fingerprint,
        expected_branch_id=config.branch_id,
    )
    vitals = VitalSigns(config).check(persist=False)
    limit_seconds = config.runtime.weekly_gpu_budget_hours * 3600.0
    runtime_seconds = memory.runtime_seconds_this_week()
    brain_seconds = memory.brain_seconds_this_week()
    world = tools.world_model.snapshot(64)
    incomplete = tools.state_bus.incomplete(64)
    metabolism = _metabolism(config)
    homeostasis = HomeostasisEngine(
        state_dir,
        tools.observations,
        tools.world_model,
        tools.state_bus,
        tools.body.diagnostics(),
        metabolism_snapshot=metabolism,
    ).evaluate().as_dict()
    return {
        "identity": _identity_snapshot(config),
        "lifecycle": {
            "state": memory.get_meta("lifecycle_state", "uninitialized"),
            "next_wake_at": memory.get_meta("next_wake_at"),
            "boot_count": int(memory.get_meta("boot_count", "0") or "0"),
            "preflight": preflight.as_dict(),
        },
        "continuity": {
            "chronicle_valid": chronicle_valid,
            "chronicle_error": chronicle_error,
            "vitals_healthy": vitals.healthy,
            "crc": vitals.crc,
            "continuity_comparison": vitals.continuity_comparison,
        },
        "resources": {
            "weekly_gpu_limit_hours": config.runtime.weekly_gpu_budget_hours,
            "runtime_hours_used": runtime_seconds / 3600.0,
            "brain_hours_used": brain_seconds / 3600.0,
            "runtime_hours_remaining": max(
                0.0,
                (limit_seconds - runtime_seconds) / 3600.0,
            ),
        },
        "metabolism": metabolism,
        "homeostasis": {
            "mode": homeostasis.get("mode"),
            "signals": list(homeostasis.get("signals") or [])[:12],
            "metabolism": homeostasis.get("metabolism") or {},
        },
        "world": {
            "active_belief_count": len(world.get("beliefs") or []),
            "contradictions": list(world.get("contradictions") or [])[:16],
        },
        "sensorium": _safe_sensorium(tools, 8),
        "digital_body": tools.body.diagnostics(),
        "state_bus": {
            "incomplete_count": len(incomplete),
            "incomplete_transaction_ids": [
                str(item["transaction_id"]) for item in incomplete[:16]
            ],
        },
    }


def build_mcp_server(config_path: str | Path = "config/genesis.yaml") -> Any:
    """Build the Genesis 1.2 read-oriented MCP organism port without starting transport.

    The exported port exposes sanitized organism state and world-query functions,
    never arbitrary shell execution, credentials, raw sensor payloads, mutation/
    deployment authority, or new external permissions.
    """

    _require_mcp()
    from mcp.server import MCPServer

    config_path = Path(config_path).resolve()
    config = load_config(config_path)
    server = MCPServer("ELIA WILD")

    @server.tool()
    def elia_status() -> dict[str, Any]:
        """Return sanitized continuity, physiology, metabolism, resource and body status."""
        return _status_snapshot(config)

    @server.tool()
    def elia_preflight() -> dict[str, Any]:
        """Return the model-independent wake/hibernate/halt decision."""
        identity = IdentityBundle.load(
            config.subject_core_path,
            config.continuity_constitution_path,
        )
        result = evaluate_preflight(
            config.runtime.state_dir,
            config.runtime.weekly_gpu_budget_hours,
            expected_identity_fingerprint=identity.fingerprint,
            expected_branch_id=config.branch_id,
        )
        vitals = VitalSigns(config).check(persist=False)
        payload = result.as_dict()
        payload["organism_healthy"] = vitals.healthy
        if not vitals.healthy:
            payload["mode"] = "halt"
            payload["reason"] = "Organism vital-sign audit failed."
        return payload

    @server.tool()
    def elia_world_query(
        text: str = "",
        domain: str = "",
        limit: int = 16,
    ) -> dict[str, Any]:
        """Query evidence-bearing world beliefs without changing their status."""
        tools = ToolRegistry(
            config.runtime.state_dir / "workspace",
            config.raw_tools,
        )
        beliefs = tools.world_model.query(
            text=str(text),
            domain=str(domain).strip() or None,
            limit=max(1, min(int(limit), MAX_WORLD_QUERY)),
        )
        return {
            "beliefs": [item.as_dict() for item in beliefs],
            "epistemic_rule": (
                "MCP query is read-only; returned model hypotheses are not verified facts unless their status says verified."
            ),
        }

    @server.tool()
    def elia_sensorium_recent(limit: int = 8) -> dict[str, Any]:
        """Return recent observation metadata/digests without raw sensor payloads."""
        tools = ToolRegistry(
            config.runtime.state_dir / "workspace",
            config.raw_tools,
        )
        return {"observations": _safe_sensorium(tools, limit)}

    @server.tool()
    def elia_body_diagnostics() -> dict[str, Any]:
        """Return configured digital-body capability readiness without credentials."""
        tools = ToolRegistry(
            config.runtime.state_dir / "workspace",
            config.raw_tools,
        )
        return tools.body.diagnostics()

    @server.tool()
    def elia_homeostasis() -> dict[str, Any]:
        """Return deterministic Genesis 1.2 physiology including verified metabolism."""
        tools = ToolRegistry(
            config.runtime.state_dir / "workspace",
            config.raw_tools,
        )
        return _homeostasis(config, tools)

    @server.resource("elia://identity")
    def identity_resource() -> str:
        """ELIA identity/lineage fingerprints and current body version."""
        return json.dumps(_identity_snapshot(config), ensure_ascii=False, sort_keys=True)

    @server.resource("elia://status")
    def status_resource() -> str:
        """Sanitized organism status snapshot."""
        return json.dumps(_status_snapshot(config), ensure_ascii=False, sort_keys=True)

    @server.resource("elia://sensorium/recent")
    def sensorium_resource() -> str:
        """Recent observation metadata and content digests, never raw payloads."""
        tools = ToolRegistry(
            config.runtime.state_dir / "workspace",
            config.raw_tools,
        )
        return json.dumps(
            {"observations": _safe_sensorium(tools, 8)},
            ensure_ascii=False,
            sort_keys=True,
        )

    return server


def _is_loopback_host(host: str) -> bool:
    host = str(host).strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elia-mcp")
    parser.add_argument("--config", default="config/genesis.yaml")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="stdio is the safe local default; HTTP is loopback-only in Genesis 1.2",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    server = build_mcp_server(args.config)
    if args.transport == "stdio":
        server.run()
        return

    if not _is_loopback_host(args.host):
        raise SystemExit(
            "Genesis 1.2 MCP HTTP transport is intentionally loopback-only because "
            "this server does not implement a remote authentication policy. Put an "
            "authenticated reverse proxy/tunnel in front of it instead of exposing it directly."
        )
    port = int(args.port)
    if port <= 0 or port > 65535:
        raise SystemExit("MCP port must be in 1..65535")
    server.run(
        transport="streamable-http",
        host=args.host,
        port=port,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
