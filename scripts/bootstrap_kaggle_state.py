from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from elia.checkpoint import CheckpointManager
from elia.chronicle import Chronicle
from elia.config import load_config
from elia.memory import MemoryStore
from elia.wake_transport import (
    CHECKPOINT_NAME,
    DIGEST_NAME,
    TRANSPORT_NAME,
    TransportState,
    mark_success,
    write_digest,
    write_transport_state,
)


def command(args: list[str]) -> None:
    result = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(args[:4])}\n{result.stdout[-6000:]}")
    if result.stdout:
        print(result.stdout.rstrip())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create the first ELIA private Kaggle state bundle without GPU")
    parser.add_argument("--config", default="config/genesis.yaml")
    parser.add_argument("--dataset", required=True, help="Kaggle dataset id owner/dataset-slug")
    parser.add_argument("--output", default=".bootstrap/elia-wild-state")
    parser.add_argument(
        "--create-dataset",
        action="store_true",
        help="After creating the local bundle, upload it as a new private Kaggle Dataset",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if "/" not in args.dataset or args.dataset.startswith("/") or args.dataset.endswith("/"):
        raise RuntimeError("--dataset must be owner/dataset-slug")
    key = os.getenv("ELIA_CHECKPOINT_KEY", "").strip()
    if len(key) < 16:
        raise RuntimeError("ELIA_CHECKPOINT_KEY must be set to a secret of at least 16 characters")

    repo_root = Path(__file__).resolve().parents[1]
    config = load_config(repo_root / args.config)
    output = Path(args.output).resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    with tempfile.TemporaryDirectory(prefix="elia-bootstrap-") as temp_raw:
        state_dir = Path(temp_raw) / ".elia"
        memory = MemoryStore(state_dir / "memory.sqlite3")
        chronicle = Chronicle(state_dir / "chronicle.jsonl")

        memory.remember(
            "self",
            config.identity_statement,
            importance=1.0,
            source="genesis-bootstrap",
            metadata={"immutable_seed": True},
        )
        memory.set_meta("genesis_initialized", "1")
        memory.set_meta("boot_count", "0")
        memory.set_meta("lifecycle_state", "hibernating")
        memory.set_meta("next_wake_at", datetime.now(timezone.utc).isoformat())
        chronicle.append(
            "GENESIS_SEED",
            {
                "identity": config.identity_name,
                "model_id": config.brain.model_id,
                "weekly_gpu_budget_hours": config.runtime.weekly_gpu_budget_hours,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        info = CheckpointManager(
            state_dir,
            config.identity_name,
            key.encode("utf-8"),
        ).export(output / CHECKPOINT_NAME)

    write_digest(output / DIGEST_NAME, info.digest)
    transport = mark_success(TransportState(), info.digest, info.counter)
    write_transport_state(output / TRANSPORT_NAME, transport)
    metadata = {
        "title": "ELIA WILD Private State",
        "id": args.dataset,
        "licenses": [{"name": "copyright-authors"}],
        "description": (
            "Private operational checkpoint transport for the ELIA WILD experiment. "
            "Contains authenticated agent state, not public training data."
        ),
    }
    (output / "dataset-metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output),
                "dataset": args.dataset,
                "checkpoint": str(output / CHECKPOINT_NAME),
                "digest": info.digest,
                "counter": info.counter,
                "private_by_default": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if args.create_dataset:
        if not os.getenv("KAGGLE_API_TOKEN", "").strip():
            raise RuntimeError("--create-dataset requires KAGGLE_API_TOKEN")
        command(
            [
                "kaggle",
                "datasets",
                "create",
                "--path",
                str(output),
                "--quiet",
                "--keep-tabular",
                "--dir-mode",
                "skip",
            ]
        )


if __name__ == "__main__":
    main()
