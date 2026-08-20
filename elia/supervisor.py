from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from .config import Config, load_config
from .identity import IdentityBundle
from .lifecycle import LifecycleDecision, evaluate_preflight
from .paths import resolve_entry_config
from .vitals import VitalSigns


@dataclass(frozen=True, slots=True)
class SupervisorDecision:
    action: str
    reason: str
    preflight: dict[str, Any]
    child_command: tuple[str, ...] | None = None

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        item["child_command"] = list(self.child_command) if self.child_command else None
        return item


class ResidentSupervisor:
    """Cheap model-independent lifecycle and organism supervisor.

    The supervisor itself never imports or loads a model backend. It validates organism
    anatomy/CRC plus persistent identity state, and starts one fixed ELIA child command
    only when both vital signs and CPU-only lifecycle preflight permit cognition.
    """

    def __init__(
        self,
        config_path: Path,
        *,
        heartbeat_seconds: float = 60.0,
        max_cycles: int = 8,
    ):
        self.config_path = resolve_entry_config(config_path)
        self.config: Config = load_config(self.config_path)
        self.identity = IdentityBundle.load(
            self.config.subject_core_path,
            self.config.continuity_constitution_path,
        )
        self.vitals = VitalSigns(self.config)
        self.last_vitals: dict[str, Any] | None = None
        self.heartbeat_seconds = max(5.0, min(float(heartbeat_seconds), 3600.0))
        self.max_cycles = max(1, min(int(max_cycles), 64))

    def preflight(self) -> LifecycleDecision:
        return evaluate_preflight(
            self.config.runtime.state_dir,
            self.config.runtime.weekly_gpu_budget_hours,
            expected_identity_fingerprint=self.identity.fingerprint,
            expected_branch_id=self.config.branch_id,
        )

    def child_command(self) -> tuple[str, ...]:
        return (
            sys.executable,
            "-m",
            "elia",
            "--config",
            str(self.config_path),
            "--cycles",
            str(self.max_cycles),
        )

    def decide(self) -> SupervisorDecision:
        vital_report = self.vitals.check(persist=True)
        self.last_vitals = vital_report.as_dict()
        preflight = self.preflight()
        if not vital_report.healthy:
            findings = [
                item.get("message", "")
                for item in self.last_vitals.get("organism", {}).get("findings", [])
                if item.get("severity") == "critical"
            ]
            comparison = self.last_vitals.get("continuity_comparison") or {}
            failures = list(comparison.get("critical_failures") or [])
            detail = "; ".join((findings + failures)[:4]) or "organism/continuity vital signs are not healthy"
            return SupervisorDecision(
                "halt",
                "Organism vital-sign failure before cognition: " + detail,
                preflight.as_dict(),
            )
        if preflight.mode == "halt":
            return SupervisorDecision(
                "halt",
                preflight.reason,
                preflight.as_dict(),
            )
        if preflight.mode == "hibernate":
            return SupervisorDecision(
                "sleep",
                preflight.reason,
                preflight.as_dict(),
            )
        return SupervisorDecision(
            "launch",
            preflight.reason,
            preflight.as_dict(),
            self.child_command(),
        )

    def run_child(self, command: tuple[str, ...]) -> dict[str, Any]:
        started = datetime.now(timezone.utc).isoformat()
        result = subprocess.run(
            list(command),
            cwd=str(self.config_path.parent.parent),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            env=os.environ.copy(),
        )
        output = result.stdout or ""
        return {
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "returncode": result.returncode,
            "output_tail": output[-12000:],
        }

    def heartbeat(self, *, execute: bool = True) -> dict[str, Any]:
        decision = self.decide()
        report: dict[str, Any] = {
            "decision": decision.as_dict(),
            "vitals": self.last_vitals,
        }
        if execute and decision.action == "launch" and decision.child_command:
            report["child"] = self.run_child(decision.child_command)
        return report

    def serve_forever(self) -> None:
        while True:
            report = self.heartbeat(execute=True)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
            if report["decision"]["action"] == "halt":
                raise SystemExit(2)
            time.sleep(self.heartbeat_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elia-supervisor")
    parser.add_argument("--config", default="config/genesis.yaml")
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=float(os.getenv("ELIA_SUPERVISOR_HEARTBEAT", "60")),
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=int(os.getenv("ELIA_SUPERVISOR_MAX_CYCLES", "8")),
    )
    parser.add_argument("--once", action="store_true", help="Run one supervisor heartbeat and exit")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate vital signs/lifecycle without starting the cognitive child",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    supervisor = ResidentSupervisor(
        Path(args.config),
        heartbeat_seconds=args.heartbeat_seconds,
        max_cycles=args.max_cycles,
    )
    if args.once or args.dry_run:
        report = supervisor.heartbeat(execute=not args.dry_run)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(2 if report["decision"]["action"] == "halt" else 0)
    supervisor.serve_forever()


if __name__ == "__main__":
    main()
