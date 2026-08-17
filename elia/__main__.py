from __future__ import annotations

import argparse
import json

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
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    state_dir = config.runtime.state_dir

    if args.verify:
        valid, error = Chronicle(state_dir / "chronicle.jsonl").verify()
        print(json.dumps({"valid": valid, "error": error}, indent=2))
        raise SystemExit(0 if valid else 2)

    if args.status:
        memory = MemoryStore(state_dir / "memory.sqlite3")
        limit = config.runtime.weekly_gpu_budget_hours
        runtime_hours = memory.runtime_seconds_this_week() / 3600.0
        brain_hours = memory.brain_seconds_this_week() / 3600.0
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
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    runtime = EliaRuntime(config)
    runtime.run(cycles=args.cycles)


if __name__ == "__main__":
    main()
