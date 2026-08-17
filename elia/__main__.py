from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .checkpoint import CheckpointError, CheckpointManager
from .chronicle import Chronicle
from .config import load_config
from .memory import MemoryStore
from .runtime import EliaRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elia-wild")
    parser.add_argument("--config", default="config/genesis.yaml", help="Path to Genesis YAML config")
    parser.add_argument("--cycles", type=int, default=None, help="Run N cycles, then exit")
    parser.add_argument("--verify", action="store_true", help="Verify Chronicle and exit")
    parser.add_argument("--status", action="store_true", help="Print persistent runtime status and exit")
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


def _checkpoint_manager(config, key_env: str) -> CheckpointManager:
    key = os.getenv(key_env)
    if not key:
        raise SystemExit(
            f"checkpoint operation requires environment variable {key_env!r}; "
            "keep this secret outside GitHub and outside checkpoint archives"
        )
    return CheckpointManager(config.runtime.state_dir, config.identity_name, key.encode("utf-8"))


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    state_dir = config.runtime.state_dir

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

    if args.status:
        memory = MemoryStore(state_dir / "memory.sqlite3")
        limit = config.runtime.weekly_gpu_budget_hours
        runtime_hours = memory.runtime_seconds_this_week() / 3600.0
        brain_hours = memory.brain_seconds_this_week() / 3600.0
        anchor_path = state_dir / "checkpoint.anchor.json"
        anchor = None
        if anchor_path.exists():
            try:
                anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                anchor = {"error": "invalid checkpoint anchor"}
        print(
            json.dumps(
                {
                    "identity": config.identity_name,
                    "boot_count": int(memory.get_meta("boot_count", "0") or "0"),
                    "weekly_gpu_budget_hours": limit,
                    "gpu_runtime_hours_used": runtime_hours,
                    "gpu_runtime_hours_remaining": max(0.0, limit - runtime_hours),
                    "brain_inference_hours_used": brain_hours,
                    "memory_records": len(memory.recent(1000000)),
                    "checkpoint_anchor": anchor,
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

    runtime = EliaRuntime(config)
    runtime.run(cycles=args.cycles)


if __name__ == "__main__":
    main()
