from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .brain import MockBrain
from .checkpoint import CheckpointManager
from .config import Config, load_config
from .identity import IdentityBundle
from .organism_runtime import OrganismRuntime
from .vitals import VitalSigns


def bootstrap(config: Config, *, cycles: int = 2, checkpoint_path: Path | None = None) -> dict[str, Any]:
    """Initialize/continue a zero-GPU ELIA state and prove the organism path.

    The bootstrap uses the deterministic MockBrain explicitly, so it never imports the
    configured expensive model backend. It exercises the current OrganismRuntime,
    including durable sensorium/world/causal wiring, on a fresh CPU machine.
    """

    before = VitalSigns(config).check(persist=True)
    if not before.healthy:
        return {
            "ok": False,
            "stage": "pre_boot_vitals",
            "vitals": before.as_dict(),
            "brain_backend_used": "none",
        }

    runtime = OrganismRuntime(config, brain=MockBrain())
    outcome = runtime.run(cycles=max(1, min(int(cycles), 16)))
    after = VitalSigns(config).check(persist=True)
    result: dict[str, Any] = {
        "ok": after.healthy,
        "identity_fingerprint": runtime.identity.fingerprint,
        "outcome": outcome,
        "vitals": after.as_dict(),
        "brain_backend_used": "mock",
        "configured_brain_backend_not_loaded": config.brain.backend,
        "world_model": runtime.tools.world_model.snapshot(12),
        "sensorium": runtime.tools.observations.snapshot(8),
        "causal_memory": runtime.tools.causal.snapshot(8),
        "digital_body": runtime.tools.body.diagnostics(),
        "state_bus_incomplete": runtime.tools.state_bus.incomplete(16),
    }

    if checkpoint_path is not None:
        key = os.getenv("ELIA_CHECKPOINT_KEY", "").strip()
        if not key:
            result["checkpoint"] = {
                "ok": False,
                "path": str(checkpoint_path),
                "error": "ELIA_CHECKPOINT_KEY is required for authenticated checkpoint export",
            }
        else:
            identity = IdentityBundle.load(
                config.subject_core_path,
                config.continuity_constitution_path,
            )
            info = CheckpointManager(
                config.runtime.state_dir,
                config.identity_name,
                key.encode("utf-8"),
                identity_fingerprint=identity.fingerprint,
            ).export(Path(checkpoint_path))
            result["checkpoint"] = {"ok": True, **info.as_dict()}
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elia-bootstrap")
    parser.add_argument("--config", default="config/genesis.yaml")
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--checkpoint", default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = bootstrap(
        load_config(args.config),
        cycles=args.cycles,
        checkpoint_path=Path(args.checkpoint) if args.checkpoint else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if result.get("ok") else 2)


if __name__ == "__main__":
    main()
