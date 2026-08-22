from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib import import_module
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import ModuleType
from typing import Any, Iterator

from .config import Config, load_config
from .identity import IdentityBundle
from .lifecycle import LifecycleDecision, evaluate_preflight
from .owner_control import owner_kill_active
from .paths import resolve_entry_config
from .vitals import VitalSigns

fcntl: ModuleType | None = None
try:  # Linux is the production target.
    fcntl = import_module("fcntl")
except ImportError:  # pragma: no cover
    pass


class SupervisorAlreadyRunning(RuntimeError):
    pass


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
        child_timeout_seconds: float = 1800.0,
        termination_grace_seconds: float = 5.0,
    ):
        self.config_path = resolve_entry_config(config_path)
        self.config: Config = load_config(self.config_path)
        self.identity = IdentityBundle.load(
            self.config.subject_core_path,
            self.config.continuity_constitution_path,
        )
        # VitalSigns opens durable stores, so it must be constructed only after the
        # lifecycle layer has recovered any interrupted checkpoint/transition.
        self.vitals: Any | None = None
        self.last_vitals: dict[str, Any] | None = None
        self.heartbeat_seconds = max(5.0, min(float(heartbeat_seconds), 3600.0))
        self.max_cycles = max(1, min(int(max_cycles), 64))
        self.child_timeout_seconds = max(
            1.0, min(float(child_timeout_seconds), 86400.0)
        )
        self.termination_grace_seconds = max(
            0.1, min(float(termination_grace_seconds), 60.0)
        )
        self._child_lock = threading.Lock()
        state = self.config.runtime.state_dir.resolve()
        self.singleton_lock_path = (
            state.parent / f".{state.name}.supervisor.lock"
        )

    @contextmanager
    def singleton(self) -> Iterator[None]:
        """Hold the one-resident-supervisor lease for this organism."""

        if fcntl is None:  # pragma: no cover - Linux is the production contract.
            raise RuntimeError("resident supervisor singleton locking requires fcntl")
        self.singleton_lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.singleton_lock_path.open("a+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SupervisorAlreadyRunning(
                    f"another resident supervisor owns {self.singleton_lock_path}"
                ) from exc
            handle.seek(0)
            handle.truncate()
            handle.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "acquired_at": datetime.now(timezone.utc).isoformat(),
                    },
                    sort_keys=True,
                ).encode("utf-8")
            )
            handle.flush()
            os.fsync(handle.fileno())
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

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
        # Lifecycle recovery must run before VitalSigns constructs any durable-store
        # readers. A state directory can have been atomically replaced since the prior
        # heartbeat, so production instances are rebuilt on every decision.
        preflight = self.preflight()
        if preflight.mode == "halt":
            return SupervisorDecision(
                "halt",
                preflight.reason,
                preflight.as_dict(),
            )
        vitals = self.vitals
        if vitals is None or isinstance(vitals, VitalSigns):
            vitals = VitalSigns(self.config)
            self.vitals = vitals
        vital_report = vitals.check(persist=True)
        self.last_vitals = vital_report.as_dict()
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

    @staticmethod
    def _signal_process_group(process: subprocess.Popen[Any], sig: int) -> None:
        if process.poll() is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                return
        elif sig == signal.SIGTERM:  # pragma: no cover - production is POSIX.
            process.terminate()
        else:  # pragma: no cover
            process.kill()

    def _stop_child(self, process: subprocess.Popen[Any]) -> None:
        self._signal_process_group(process, signal.SIGTERM)
        deadline = time.monotonic() + self.termination_grace_seconds
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if process.poll() is None:
            self._signal_process_group(process, signal.SIGKILL)
        try:
            process.wait(timeout=max(1.0, self.termination_grace_seconds))
        except subprocess.TimeoutExpired:
            # SIGKILL should make this unreachable on the POSIX production target.
            self._signal_process_group(process, signal.SIGKILL)
            process.wait()

    def run_child(self, command: tuple[str, ...]) -> dict[str, Any]:
        started = datetime.now(timezone.utc).isoformat()
        database = self.config.runtime.state_dir / "memory.sqlite3"
        if owner_kill_active(database):
            return {
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "returncode": None,
                "output_tail": "",
                "timed_out": False,
                "terminated_by_owner": True,
                "reason": "owner kill was active before child launch",
            }
        if not self._child_lock.acquire(blocking=False):
            raise RuntimeError("a cognitive child is already active in this supervisor")
        try:
            with tempfile.TemporaryFile(mode="w+b") as output_file:
                process = subprocess.Popen(
                    list(command),
                    cwd=str(self.config_path.parent.parent),
                    stdout=output_file,
                    stderr=subprocess.STDOUT,
                    env=os.environ.copy(),
                    start_new_session=(os.name == "posix"),
                )
                deadline = time.monotonic() + self.child_timeout_seconds
                timed_out = False
                terminated_by_owner = False
                while process.poll() is None:
                    if owner_kill_active(database):
                        terminated_by_owner = True
                        self._stop_child(process)
                        break
                    if time.monotonic() >= deadline:
                        timed_out = True
                        self._stop_child(process)
                        break
                    time.sleep(0.1)
                returncode = process.wait()
                output_file.seek(0, os.SEEK_END)
                size = output_file.tell()
                output_file.seek(max(0, size - 12000))
                output = output_file.read().decode("utf-8", errors="replace")
            reason = None
            if terminated_by_owner:
                reason = "owner kill activated while cognitive child was running"
            elif timed_out:
                reason = (
                    f"cognitive child exceeded {self.child_timeout_seconds:.3f}s timeout"
                )
            return {
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "returncode": returncode,
                "output_tail": output[-12000:],
                "timed_out": timed_out,
                "terminated_by_owner": terminated_by_owner,
                "reason": reason,
            }
        finally:
            self._child_lock.release()

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
            child = report.get("child") or {}
            if child.get("terminated_by_owner"):
                raise SystemExit(2)
            if child.get("timed_out"):
                raise SystemExit(3)
            deadline = time.monotonic() + self.heartbeat_seconds
            while time.monotonic() < deadline:
                if owner_kill_active(
                    self.config.runtime.state_dir / "memory.sqlite3"
                ):
                    raise SystemExit(2)
                time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))


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
    parser.add_argument(
        "--child-timeout-seconds",
        type=float,
        default=float(os.getenv("ELIA_SUPERVISOR_CHILD_TIMEOUT", "1800")),
    )
    parser.add_argument(
        "--termination-grace-seconds",
        type=float,
        default=float(os.getenv("ELIA_SUPERVISOR_TERMINATION_GRACE", "5")),
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
        child_timeout_seconds=args.child_timeout_seconds,
        termination_grace_seconds=args.termination_grace_seconds,
    )
    try:
        with supervisor.singleton():
            if args.once or args.dry_run:
                report = supervisor.heartbeat(execute=not args.dry_run)
                print(json.dumps(report, ensure_ascii=False, indent=2))
                child = report.get("child") or {}
                failed = (
                    report["decision"]["action"] == "halt"
                    or child.get("timed_out")
                    or child.get("terminated_by_owner")
                    or (
                        child.get("returncode") is not None
                        and int(child["returncode"]) != 0
                    )
                )
                raise SystemExit(2 if failed else 0)
            supervisor.serve_forever()
    except SupervisorAlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(4) from exc


if __name__ == "__main__":
    main()
