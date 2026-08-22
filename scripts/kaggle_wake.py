from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any

from elia.checkpoint import CheckpointManager
from elia.config import load_config
from elia.lifecycle import evaluate_preflight
from elia.paths import data_root
from elia.wake_anchor import WakeTrustAnchorStore, default_anchor_path
from elia.wake_transport import (
    CHECKPOINT_NAME,
    DIGEST_NAME,
    RELAY_REPORT_NAME,
    TRANSPORT_NAME,
    TransportState,
    build_kernel_metadata,
    launch_suppressed,
    locate_state_bundle,
    mark_failure,
    mark_operator_reset,
    mark_pending,
    mark_success,
    parse_dataset_status,
    parse_kernel_status,
    read_digest,
    read_transport_state,
    render_runner,
    validate_relay_report,
    write_digest,
    write_transport_state,
)


FAILURE_THRESHOLD = 3
PENDING_TIMEOUT_SECONDS = 8 * 3600
DATASET_READY_TIMEOUT_SECONDS = 180
DATASET_POLL_SECONDS = 5
EXIT_PREFLIGHT_HALT = 2
EXIT_DEGRADED = 3
EXIT_TRANSPORT_FAILURE = 4
EXIT_CIRCUIT_OPEN = 5

_CONTINUITY_ENV = (
    "ELIA_CHECKPOINT_KEY",
    "ELIA_CHECKPOINT_ENCRYPTION_KEY",
    "ELIA_CHECKPOINT_REQUIRE_ENCRYPTION",
    "ELIA_WAKE_RESET_AUTH",
)


def _kaggle_child_env() -> dict[str, str]:
    """Give Kaggle CLI its credential without delegating ELIA continuity keys."""
    env = os.environ.copy()
    for name in _CONTINUITY_ENV:
        env.pop(name, None)
    return env


def command(
    args: list[str], *, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env=_kaggle_child_env(),
    )
    if check and result.returncode != 0:
        tail = (result.stdout or "")[-6000:]
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args[:4])}\n{tail}"
        )
    return result


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    return value


def print_event(event: str, **payload: Any) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False, sort_keys=True))


def download_state_dataset(
    dataset: str, destination: Path
) -> tuple[Path, Path, Path | None]:
    destination.mkdir(parents=True, exist_ok=True)
    command(
        [
            "kaggle",
            "datasets",
            "download",
            dataset,
            "--path",
            str(destination),
            "--unzip",
            "--force",
            "--quiet",
        ]
    )
    return locate_state_bundle(destination)


def dataset_status(dataset: str) -> tuple[str, str]:
    result = command(
        ["kaggle", "datasets", "status", dataset, "--format", "json"],
        check=False,
    )
    output = (result.stdout or "").strip()
    return (
        (parse_dataset_status(output), output)
        if result.returncode == 0
        else ("unknown", output)
    )


def wait_dataset_ready(
    dataset: str,
    *,
    timeout_seconds: float = DATASET_READY_TIMEOUT_SECONDS,
    poll_seconds: float = DATASET_POLL_SECONDS,
) -> None:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    observations: list[str] = []
    while time.monotonic() < deadline:
        state, raw = dataset_status(dataset)
        observations.append(f"{state}:{raw[-400:]}")
        if state == "ready":
            return
        if state == "failed":
            raise RuntimeError(
                f"Kaggle state Dataset entered a failed state: {raw[-2000:]}"
            )
        time.sleep(max(1.0, min(float(poll_seconds), 30.0)))
    raise RuntimeError(
        "Kaggle state Dataset did not become ready before timeout; last observations: "
        + " | ".join(observations[-4:])
    )


def dataset_upload_dir(
    dataset: str,
    checkpoint: Path,
    digest: str,
    transport: TransportState,
    destination: Path,
    *,
    transport_key: bytes | str,
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(checkpoint, destination / CHECKPOINT_NAME)
    write_digest(destination / DIGEST_NAME, digest)
    write_transport_state(
        destination / TRANSPORT_NAME,
        transport,
        key=transport_key,
        require_auth=True,
    )
    command(
        ["kaggle", "datasets", "metadata", dataset, "--path", str(destination)]
    )
    if not (destination / "dataset-metadata.json").is_file():
        raise RuntimeError("Kaggle did not return dataset-metadata.json")
    return destination


def version_state_dataset(
    dataset: str,
    checkpoint: Path,
    digest: str,
    transport: TransportState,
    *,
    message: str,
    root: Path,
    transport_key: bytes | str,
) -> None:
    upload = root / "dataset-upload"
    shutil.rmtree(upload, ignore_errors=True)
    dataset_upload_dir(
        dataset,
        checkpoint,
        digest,
        transport,
        upload,
        transport_key=transport_key,
    )
    command(
        [
            "kaggle",
            "datasets",
            "version",
            "--path",
            str(upload),
            "--message",
            message[:200],
            "--quiet",
            "--keep-tabular",
            "--dir-mode",
            "skip",
        ]
    )
    wait_dataset_ready(dataset)


def pending_age_seconds(state: TransportState) -> float | None:
    if not state.pending_since:
        return None
    try:
        moment = datetime.fromisoformat(state.pending_since)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(
        0.0,
        (datetime.now(timezone.utc) - moment.astimezone(timezone.utc)).total_seconds(),
    )


def _strict_manager(state_dir: Path, identity_name: str, key: str) -> CheckpointManager:
    return CheckpointManager(
        state_dir,
        identity_name,
        key.encode("utf-8"),
        require_encryption=True,
    )


def inspect_restore(
    *,
    checkpoint: Path,
    digest: str,
    key: str,
    identity_name: str,
    state_dir: Path,
) -> tuple[CheckpointManager, Any]:
    shutil.rmtree(state_dir, ignore_errors=True)
    manager = _strict_manager(state_dir, identity_name, key)
    info = manager.inspect(checkpoint, expected_digest=digest)
    manager.restore(checkpoint, expected_digest=digest)
    return manager, info


def kernel_status(kernel: str) -> tuple[str, str]:
    result = command(["kaggle", "kernels", "status", kernel], check=False)
    output = (result.stdout or "").strip()
    return (
        (parse_kernel_status(output), output)
        if result.returncode == 0
        else ("unknown", output)
    )


def download_kernel_output(kernel: str, destination: Path) -> Path:
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)
    command(
        [
            "kaggle",
            "kernels",
            "output",
            kernel,
            "--path",
            str(destination),
            "--force",
            "--quiet",
        ]
    )
    return destination


def accept_completed_output(
    *,
    kernel: str,
    state: TransportState,
    source_digest: str,
    source_counter: int,
    key: str,
    identity_name: str,
    dataset: str,
    root: Path,
    trust_anchor: WakeTrustAnchorStore,
) -> tuple[Path, str, TransportState]:
    output_root = download_kernel_output(kernel, root / "kernel-output")
    output_checkpoint, output_digest_file, _ = locate_state_bundle(output_root)
    reports = [path for path in output_root.rglob(RELAY_REPORT_NAME) if path.is_file()]
    if len(reports) != 1:
        raise RuntimeError("completed kernel output has no unique relay-report.json")
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise RuntimeError("relay-report.json must contain a JSON object")
    if not state.pending_launch_nonce:
        raise RuntimeError(
            "completed output cannot be accepted without a pending launch nonce"
        )

    report_digest, report_counter = validate_relay_report(
        report,
        expected_nonce=state.pending_launch_nonce,
        expected_source_digest=source_digest,
    )
    if report.get("encrypted_checkpoint") is not True:
        raise RuntimeError("completed kernel did not attest encrypted checkpoint output")
    file_digest = read_digest(output_digest_file)
    if report_digest != file_digest:
        raise RuntimeError("relay report digest and trusted-digest.txt disagree")

    output_info = _strict_manager(
        root / "inspect-only", identity_name, key
    ).inspect(output_checkpoint, expected_digest=file_digest)
    if output_info.digest != report_digest or output_info.counter != report_counter:
        raise RuntimeError("output checkpoint metadata disagrees with relay report")

    cognition_started = bool(report.get("cognition_started"))
    if cognition_started and output_info.counter <= source_counter:
        raise RuntimeError(
            "cognitive run did not advance the authenticated checkpoint counter"
        )
    if output_info.counter < source_counter:
        raise RuntimeError("kernel returned an older checkpoint counter")

    accepted = mark_success(state, output_info.digest, output_info.counter)
    version_state_dataset(
        dataset,
        output_checkpoint,
        output_info.digest,
        accepted,
        message=f"ELIA relay accepted encrypted checkpoint {output_info.counter}",
        root=root,
        transport_key=key,
    )
    # The Dataset is now durable. Advance the separately persisted witness only after
    # all output validation has succeeded. If witness persistence later fails, the next
    # heartbeat will fail closed because source verification requires exact equality.
    trust_anchor.advance(counter=output_info.counter, digest=output_info.digest)
    print_event(
        "relay_accepted",
        digest=output_info.digest,
        counter=output_info.counter,
        cognition_started=cognition_started,
        encrypted_checkpoint=True,
    )
    return output_checkpoint, output_info.digest, accepted


def prepare_kernel(
    *,
    repo_root: Path,
    destination: Path,
    kernel_id: str,
    state_dataset: str,
    accelerator: str,
    source_digest: str,
    nonce: str,
    repo_ref: str,
    max_cycles: int,
) -> Path:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", repo_ref):
        raise ValueError("repo_ref must be an immutable 40-hex Git commit SHA")
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)
    template = (repo_root / "runtime" / "kaggle" / "runner_template.py").read_text(
        encoding="utf-8"
    )
    runner = render_runner(
        template,
        {
            "version": 1,
            "launch_nonce": nonce,
            "source_digest": source_digest,
            "repo_url": "https://github.com/vvseweedno/ELIA-WILD.git",
            "repo_ref": repo_ref,
            "max_cycles": max_cycles,
        },
    )
    (destination / "elia_wild_runner.py").write_text(runner, encoding="utf-8")
    metadata = build_kernel_metadata(
        kernel_id=kernel_id,
        state_dataset=state_dataset,
        accelerator=accelerator,
    )
    (destination / "kernel-metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ELIA WILD encrypted external wake heartbeat"
    )
    parser.add_argument("--config", default="config/genesis.yaml")
    parser.add_argument(
        "--state-dataset", default=os.getenv("ELIA_KAGGLE_STATE_DATASET", "")
    )
    parser.add_argument("--kernel", default=os.getenv("ELIA_KAGGLE_KERNEL", ""))
    parser.add_argument(
        "--accelerator",
        default=os.getenv("ELIA_KAGGLE_ACCELERATOR", "") or "NvidiaTeslaT4",
    )
    parser.add_argument("--repo-ref", default=os.getenv("ELIA_REPO_REF", ""))
    parser.add_argument(
        "--reset-circuit",
        action="store_true",
        default=os.getenv("ELIA_WAKE_RESET_CIRCUIT", "").strip().lower()
        in {"1", "true", "yes", "on"},
        help="Clear a suppressed launch circuit after authenticated operator diagnosis",
    )
    parser.add_argument(
        "--reset-reason",
        default=os.getenv("ELIA_WAKE_RESET_REASON", ""),
        help="Incident/change reference recorded by hash in transport state",
    )
    parser.add_argument(
        "--trust-anchor",
        default=str(default_anchor_path()),
        help="Durable rollback anchor initialized by bootstrap and stored outside Kaggle",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=int(os.getenv("ELIA_WAKE_MAX_CYCLES", "8")),
    )
    parser.add_argument(
        "--kernel-timeout-seconds",
        type=int,
        default=int(os.getenv("ELIA_KAGGLE_KERNEL_TIMEOUT", "3600")),
        help="Hard Kaggle run-time ceiling for one wake burst; clamped to 10 min..2 h",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.state_dataset or "/" not in args.state_dataset:
        raise RuntimeError(
            "--state-dataset / ELIA_KAGGLE_STATE_DATASET must be owner/dataset-slug"
        )
    if not args.kernel or "/" not in args.kernel:
        raise RuntimeError(
            "--kernel / ELIA_KAGGLE_KERNEL must be owner/kernel-slug"
        )
    require_env("KAGGLE_API_TOKEN")
    key = require_env("ELIA_CHECKPOINT_KEY")
    require_env("ELIA_CHECKPOINT_ENCRYPTION_KEY")
    os.environ["ELIA_CHECKPOINT_REQUIRE_ENCRYPTION"] = "1"
    kernel_timeout = max(600, min(int(args.kernel_timeout_seconds), 7200))

    asset_root = data_root()
    config = load_config(args.config)
    trust_anchor = WakeTrustAnchorStore(
        Path(args.trust_anchor),
        key=key.encode("utf-8"),
        identity_name=config.identity_name,
        state_dataset=args.state_dataset,
    )

    with tempfile.TemporaryDirectory(prefix="elia-wake-") as temp_raw:
        root = Path(temp_raw)
        source_checkpoint, source_digest_path, transport_path = download_state_dataset(
            args.state_dataset, root / "state-download"
        )
        source_digest = read_digest(source_digest_path)
        transport = read_transport_state(
            transport_path or (root / "missing-transport.json"),
            key=key,
            require_auth=True,
        )
        _, source_info = inspect_restore(
            checkpoint=source_checkpoint,
            digest=source_digest,
            key=key,
            identity_name=config.identity_name,
            state_dir=root / "restored-state",
        )
        # The Dataset being inspected may not teach the external witness a newer state.
        # Exact equality is required before any scheduler/kernel decision is trusted.
        anchor = trust_anchor.verify(
            counter=source_info.counter,
            digest=source_digest,
        )
        print_event(
            "state_loaded",
            digest=source_digest,
            counter=source_info.counter,
            trusted_anchor_counter=anchor.counter,
            pending=bool(transport.pending_launch_nonce),
            consecutive_kernel_failures=transport.consecutive_kernel_failures,
            encrypted_checkpoint=True,
        )

        if args.reset_circuit:
            reset_auth = require_env("ELIA_WAKE_RESET_AUTH")
            if len(reset_auth) < 32:
                raise RuntimeError("ELIA_WAKE_RESET_AUTH must contain at least 32 characters")
            reset_reason = str(args.reset_reason).strip()
            if len(reset_reason) < 8:
                raise RuntimeError("--reset-reason must contain at least 8 characters")
            evidence = json.dumps(
                {
                    "reason": reset_reason,
                    "actor": os.getenv("GITHUB_ACTOR", "local-operator"),
                    "run_id": os.getenv("GITHUB_RUN_ID", "local"),
                    "ref": os.getenv("GITHUB_REF", "local"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            reset = mark_operator_reset(transport, evidence=evidence)
            version_state_dataset(
                args.state_dataset,
                source_checkpoint,
                source_digest,
                reset,
                message=f"ELIA operator circuit reset {reset.operator_reset_count}",
                root=root,
                transport_key=key,
            )
            print_event(
                "operator_circuit_reset",
                reset_count=reset.operator_reset_count,
                evidence_sha256=reset.last_operator_reset_evidence_sha256,
            )
            return 0

        if transport.pending_launch_nonce:
            status, raw_status = kernel_status(args.kernel)
            print_event(
                "pending_kernel_status", state=status, raw=raw_status[-1000:]
            )
            if status in {"running", "queued"}:
                return 0
            if status == "complete":
                try:
                    source_checkpoint, source_digest, transport = accept_completed_output(
                        kernel=args.kernel,
                        state=transport,
                        source_digest=source_digest,
                        source_counter=source_info.counter,
                        key=key,
                        identity_name=config.identity_name,
                        dataset=args.state_dataset,
                        root=root,
                        trust_anchor=trust_anchor,
                    )
                except Exception as exc:
                    failed = mark_failure(
                        transport, f"completed output rejected: {exc}"
                    )
                    version_state_dataset(
                        args.state_dataset,
                        source_checkpoint,
                        source_digest,
                        failed,
                        message="ELIA relay rejected invalid kernel output",
                        root=root,
                        transport_key=key,
                    )
                    print_event(
                        "relay_rejected",
                        error=str(exc),
                        failures=failed.consecutive_kernel_failures,
                    )
                    return EXIT_TRANSPORT_FAILURE
                _, source_info = inspect_restore(
                    checkpoint=source_checkpoint,
                    digest=source_digest,
                    key=key,
                    identity_name=config.identity_name,
                    state_dir=root / "restored-state-after-relay",
                )
            elif status == "failed":
                failed = mark_failure(
                    transport, raw_status or "Kaggle kernel reported failure"
                )
                version_state_dataset(
                    args.state_dataset,
                    source_checkpoint,
                    source_digest,
                    failed,
                    message=f"ELIA kernel failure {failed.consecutive_kernel_failures}",
                    root=root,
                    transport_key=key,
                )
                print_event(
                    "kernel_failure_recorded",
                    failures=failed.consecutive_kernel_failures,
                )
                return EXIT_TRANSPORT_FAILURE
            else:
                age = pending_age_seconds(transport)
                if age is None or age < PENDING_TIMEOUT_SECONDS:
                    print_event("pending_status_unknown", age_seconds=age)
                    return EXIT_DEGRADED
                failed = mark_failure(
                    transport,
                    "pending launch exceeded transport timeout",
                    status="timeout",
                )
                version_state_dataset(
                    args.state_dataset,
                    source_checkpoint,
                    source_digest,
                    failed,
                    message=f"ELIA pending launch timeout {failed.consecutive_kernel_failures}",
                    root=root,
                    transport_key=key,
                )
                print_event(
                    "pending_timeout_recorded",
                    failures=failed.consecutive_kernel_failures,
                )
                return EXIT_TRANSPORT_FAILURE

        if launch_suppressed(transport, FAILURE_THRESHOLD):
            print_event(
                "launch_suppressed",
                failures=transport.consecutive_kernel_failures,
                reason=(
                    "three consecutive kernel/relay failures require diagnosis before "
                    "more GPU launches"
                ),
            )
            return EXIT_CIRCUIT_OPEN

        inspect_restore(
            checkpoint=source_checkpoint,
            digest=source_digest,
            key=key,
            identity_name=config.identity_name,
            state_dir=root / "preflight-state",
        )
        preflight = evaluate_preflight(
            root / "preflight-state",
            config.runtime.weekly_gpu_budget_hours,
        )
        print_event("preflight", **preflight.as_dict())
        if preflight.mode in {"halt", "owner_halt"}:
            return EXIT_PREFLIGHT_HALT
        if preflight.mode != "wake":
            return 0

        pending = mark_pending(transport)
        pending_nonce = pending.pending_launch_nonce
        if not pending_nonce:
            raise RuntimeError("transport failed to create a pending launch nonce")
        version_state_dataset(
            args.state_dataset,
            source_checkpoint,
            source_digest,
            pending,
            message=f"ELIA wake pending {pending_nonce[:12]}",
            root=root,
            transport_key=key,
        )
        kernel_dir = prepare_kernel(
            repo_root=asset_root,
            destination=root / "kernel",
            kernel_id=args.kernel,
            state_dataset=args.state_dataset,
            accelerator=args.accelerator,
            source_digest=source_digest,
            nonce=pending_nonce,
            repo_ref=args.repo_ref,
            max_cycles=max(1, min(args.max_cycles, 64)),
        )
        try:
            pushed = command(
                [
                    "kaggle",
                    "kernels",
                    "push",
                    "--path",
                    str(kernel_dir),
                    "--acc",
                    args.accelerator,
                    "--timeout",
                    str(kernel_timeout),
                ]
            )
        except Exception as exc:
            failed = mark_failure(
                pending, f"kernel push failed: {exc}", status="push_failed"
            )
            version_state_dataset(
                args.state_dataset,
                source_checkpoint,
                source_digest,
                failed,
                message=f"ELIA launch push failure {failed.consecutive_kernel_failures}",
                root=root,
                transport_key=key,
            )
            print_event(
                "kernel_push_failed",
                error=str(exc),
                failures=failed.consecutive_kernel_failures,
            )
            return EXIT_TRANSPORT_FAILURE

        print_event(
            "kernel_launched",
            kernel=args.kernel,
            accelerator=args.accelerator,
            timeout_seconds=kernel_timeout,
            nonce=pending.pending_launch_nonce,
            output=(pushed.stdout or "")[-1000:],
            encrypted_checkpoint_required=True,
            trust_anchor=str(trust_anchor.path),
        )
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print_event("wake_transport_error", error=f"{type(exc).__name__}: {exc}")
        raise
