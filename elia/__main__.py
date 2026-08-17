from __future__ import annotations

from dataclasses import asdict
import argparse
import json
import os
from pathlib import Path
from typing import Any

from .autonomy import derive_needs
from .checkpoint import CheckpointError, CheckpointManager
from .chronicle import Chronicle
from .config import load_config
from .economy import EconomyStore
from .identity import IdentityBundle, IdentityStore
from .lifecycle import evaluate_preflight
from .memory import MemoryStore
from .prompting import PromptTemplate
from .runtime import EliaRuntime
from .skills import SkillRegistry
from .tools import ToolRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elia-wild")
    parser.add_argument("--config", default="config/genesis.yaml", help="Path to Genesis YAML config")
    parser.add_argument("--cycles", type=int, default=None, help="Run N cycles, then exit")
    parser.add_argument("--verify", action="store_true", help="Verify Chronicle and exit")
    parser.add_argument("--status", action="store_true", help="Print persistent organism status and exit")
    parser.add_argument("--identity-report", action="store_true", help="Print identity fingerprints, self-model and lineage head")
    parser.add_argument("--preflight", action="store_true", help="Run CPU-only wake/hibernate/halt decision and exit")
    parser.add_argument(
        "--force-wake",
        action="store_true",
        help="Bypass a future wake timestamp only; integrity, identity and GPU budget guards still apply",
    )
    parser.add_argument("--checkpoint-export", metavar="PATH", help="Create an authenticated state checkpoint")
    parser.add_argument("--checkpoint-restore", metavar="PATH", help="Restore an authenticated state checkpoint")
    parser.add_argument("--checkpoint-inspect", metavar="PATH", help="Verify and inspect a checkpoint without restoring")
    parser.add_argument(
        "--checkpoint-key-env",
        default="ELIA_CHECKPOINT_KEY",
        help="Environment variable containing the checkpoint authentication key",
    )
    parser.add_argument(
        "--expected-checkpoint-digest",
        default=None,
        help="Trusted checkpoint digest required on fresh-machine restore/inspection",
    )
    return parser


def _identity_bundle(config) -> IdentityBundle:
    return IdentityBundle.load(
        config.subject_core_path,
        config.continuity_constitution_path,
    )


def _checkpoint_manager(config, key_env: str) -> CheckpointManager:
    key = os.getenv(key_env)
    if not key:
        raise SystemExit(
            f"checkpoint operation requires environment variable {key_env!r}; "
            "keep this secret outside GitHub and outside checkpoint archives"
        )
    identity = _identity_bundle(config)
    return CheckpointManager(
        config.runtime.state_dir,
        config.identity_name,
        key.encode("utf-8"),
        identity_fingerprint=identity.fingerprint,
    )


def _maybe_auto_checkpoint(config, key_env: str, outcome: dict[str, Any]) -> dict[str, Any] | None:
    destination = config.runtime.auto_checkpoint_path
    if destination is None:
        return None
    if outcome.get("state") not in {"hibernating", "paused"}:
        return None
    key = os.getenv(key_env)
    if not key:
        return {
            "ok": False,
            "path": str(destination),
            "error": f"auto-checkpoint configured but {key_env} is not set",
        }
    identity = _identity_bundle(config)
    try:
        info = CheckpointManager(
            config.runtime.state_dir,
            config.identity_name,
            key.encode("utf-8"),
            identity_fingerprint=identity.fingerprint,
        ).export(destination)
    except CheckpointError as exc:
        return {"ok": False, "path": str(destination), "error": str(exc)}
    return {"ok": True, **info.as_dict()}


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    state_dir = config.runtime.state_dir
    identity = _identity_bundle(config)

    checkpoint_modes = [args.checkpoint_export, args.checkpoint_restore, args.checkpoint_inspect]
    if sum(value is not None for value in checkpoint_modes) > 1:
        raise SystemExit("choose only one checkpoint operation at a time")

    if args.checkpoint_restore:
        manager = _checkpoint_manager(config, args.checkpoint_key_env)
        try:
            info = manager.restore(
                Path(args.checkpoint_restore),
                expected_digest=args.expected_checkpoint_digest,
            )
        except CheckpointError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            raise SystemExit(2) from exc
        print(json.dumps({"ok": True, "restored": info.as_dict()}, ensure_ascii=False, indent=2))
        return

    if args.checkpoint_inspect:
        manager = _checkpoint_manager(config, args.checkpoint_key_env)
        try:
            info = manager.inspect(
                Path(args.checkpoint_inspect),
                expected_digest=args.expected_checkpoint_digest,
            )
        except CheckpointError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            raise SystemExit(2) from exc
        print(json.dumps({"ok": True, "checkpoint": info.as_dict()}, ensure_ascii=False, indent=2))
        return

    if args.verify:
        valid, error = Chronicle(state_dir / "chronicle.jsonl").verify()
        print(json.dumps({"valid": valid, "error": error}, indent=2))
        raise SystemExit(0 if valid else 2)

    preflight = evaluate_preflight(
        state_dir,
        config.runtime.weekly_gpu_budget_hours,
        force_wake=args.force_wake,
        expected_identity_fingerprint=identity.fingerprint,
        expected_branch_id=config.branch_id,
    )

    if args.preflight:
        print(json.dumps(preflight.as_dict(), ensure_ascii=False, indent=2))
        raise SystemExit(2 if preflight.mode == "halt" else 0)

    if args.status or args.identity_report:
        prompt_template = PromptTemplate.load(config.system_prompt_path)
        memory = MemoryStore(state_dir / "memory.sqlite3")
        identity_store = IdentityStore(state_dir / "memory.sqlite3")
        economy_store = EconomyStore(state_dir / "memory.sqlite3")
        tools = ToolRegistry(state_dir / "workspace", config.raw_tools)
        skills = SkillRegistry(config.skills_dir)
        capability_catalog = tools.catalog()
        capability_health = memory.capability_health_all(list(capability_catalog), window=20)
        skill_state = skills.prompt_catalog(capability_catalog, capability_health)
        economy = economy_store.snapshot(16)
        limit = config.runtime.weekly_gpu_budget_hours
        runtime_hours = memory.runtime_seconds_this_week() / 3600.0
        brain_hours = memory.brain_seconds_this_week() / 3600.0
        resources = {
            "weekly_limit_hours": limit,
            "runtime_hours_used": runtime_hours,
            "brain_hours_used": brain_hours,
            "runtime_hours_remaining": max(0.0, limit - runtime_hours),
        }
        active_goals = memory.active_goals(16)
        chronicle_valid, chronicle_error = Chronicle(state_dir / "chronicle.jsonl").verify()
        needs = [
            need.as_dict()
            for need in derive_needs(
                memory,
                chronicle_valid=chronicle_valid,
                budget=resources,
                active_goals=active_goals,
                capability_health=capability_health,
                economy=economy,
            )
        ]
        anchor_path = state_dir / "checkpoint.anchor.json"
        anchor = None
        if anchor_path.exists():
            try:
                anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                anchor = {"error": "invalid checkpoint anchor"}
        last_lineage = identity_store.last_lineage()
        identity_report = {
            "name": identity.name,
            "identity_id": identity.identity_id,
            "branch_id": config.branch_id,
            "bundle_fingerprint": identity.fingerprint,
            "subject_core_fingerprint": identity.subject_core_fingerprint,
            "constitution_fingerprint": identity.constitution_fingerprint,
            "prompt_fingerprint": prompt_template.fingerprint,
            "latest_self_model": identity_store.latest_self_model(),
            "lineage_head": asdict(last_lineage) if last_lineage is not None else None,
            "last_drift_report": memory.get_meta("last_drift_report"),
        }
        if args.identity_report:
            print(json.dumps(identity_report, ensure_ascii=False, indent=2))
            return
        print(
            json.dumps(
                {
                    "identity": identity_report,
                    "boot_count": int(memory.get_meta("boot_count", "0") or "0"),
                    "lifecycle": {
                        "state": memory.get_meta("lifecycle_state", "uninitialized"),
                        "last_hibernated_at": memory.get_meta("last_hibernated_at"),
                        "last_brain_loaded_at": memory.get_meta("last_brain_loaded_at"),
                        "preflight": preflight.as_dict(),
                    },
                    "chronicle": {"valid": chronicle_valid, "error": chronicle_error},
                    "resources": resources,
                    "economy": economy,
                    "memory_records": len(memory.recent(1000000)),
                    "active_goals": [asdict(goal) for goal in active_goals],
                    "needs": needs,
                    "scheduler": {
                        "next_wake_at": memory.get_meta("next_wake_at"),
                        "last_sleep_seconds": memory.get_meta("last_sleep_seconds"),
                    },
                    "capabilities": {
                        "catalog": capability_catalog,
                        "health": capability_health,
                    },
                    "skills": skill_state,
                    "checkpoint_anchor": anchor,
                    "auto_checkpoint_path": (
                        str(config.runtime.auto_checkpoint_path)
                        if config.runtime.auto_checkpoint_path is not None
                        else None
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.checkpoint_export:
        manager = _checkpoint_manager(config, args.checkpoint_key_env)
        try:
            info = manager.export(Path(args.checkpoint_export))
        except CheckpointError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            raise SystemExit(2) from exc
        print(json.dumps({"ok": True, "checkpoint": info.as_dict()}, ensure_ascii=False, indent=2))
        return

    if preflight.mode != "wake":
        print(
            json.dumps(
                {
                    "state": preflight.mode,
                    "preflight": preflight.as_dict(),
                    "brain_loaded": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(2 if preflight.mode == "halt" else 0)

    runtime = EliaRuntime(config)
    outcome = runtime.run(cycles=args.cycles)
    auto_checkpoint = _maybe_auto_checkpoint(config, args.checkpoint_key_env, outcome)
    output: dict[str, Any] = {
        "preflight": preflight.as_dict(),
        "outcome": outcome,
        "brain_loaded": runtime.brain_loaded,
        "identity_fingerprint": runtime.identity.fingerprint,
        "self_model_fingerprint": runtime.memory.get_meta("self_model_fingerprint"),
    }
    if auto_checkpoint is not None:
        output["auto_checkpoint"] = auto_checkpoint
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
