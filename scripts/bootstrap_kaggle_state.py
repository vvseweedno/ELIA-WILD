from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from elia import __version__
from elia.checkpoint import CheckpointManager
from elia.chronicle import Chronicle
from elia.config import load_config
from elia.identity import IdentityBundle, IdentityStore, build_self_model_snapshot
from elia.memory import MemoryStore
from elia.prompting import PromptTemplate
from elia.tools import ToolRegistry
from elia.wake_anchor import WakeTrustAnchorStore, default_anchor_path
from elia.wake_transport import (
    CHECKPOINT_NAME,
    DIGEST_NAME,
    TRANSPORT_NAME,
    TransportState,
    mark_success,
    write_digest,
    write_transport_state,
)


def _kaggle_child_env() -> dict[str, str]:
    env = os.environ.copy()
    # Kaggle CLI needs only its own API credential. Never delegate identity-state
    # authentication or encryption keys to an unrelated child process.
    env.pop("ELIA_CHECKPOINT_KEY", None)
    env.pop("ELIA_CHECKPOINT_ENCRYPTION_KEY", None)
    env.pop("ELIA_CHECKPOINT_REQUIRE_ENCRYPTION", None)
    return env


def command(args: list[str]) -> None:
    result = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env=_kaggle_child_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args[:4])}\n{result.stdout[-6000:]}"
        )
    if result.stdout:
        print(result.stdout.rstrip())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the first encrypted ELIA private Kaggle state bundle without GPU"
    )
    parser.add_argument("--config", default="config/genesis.yaml")
    parser.add_argument("--dataset", required=True, help="Kaggle dataset id owner/dataset-slug")
    parser.add_argument("--output", default=".bootstrap/elia-wild-state")
    parser.add_argument(
        "--trust-anchor",
        default=str(default_anchor_path()),
        help="Durable relay-host rollback anchor kept outside the Kaggle Dataset",
    )
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
    if not os.getenv("ELIA_CHECKPOINT_ENCRYPTION_KEY", "").strip():
        raise RuntimeError(
            "ELIA_CHECKPOINT_ENCRYPTION_KEY must be set to a base64-encoded 32-byte key"
        )

    repo_root = Path(__file__).resolve().parents[1]
    config = load_config(repo_root / args.config)
    identity = IdentityBundle.load(
        config.subject_core_path,
        config.continuity_constitution_path,
    )
    prompt = PromptTemplate.load(config.system_prompt_path)
    if identity.name != config.identity_name:
        raise RuntimeError("configured identity name differs from Subject Core")

    output = Path(args.output).resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    with tempfile.TemporaryDirectory(prefix="elia-bootstrap-") as temp_raw:
        state_dir = Path(temp_raw) / ".elia"
        database = state_dir / "memory.sqlite3"
        memory = MemoryStore(database)
        identity_store = IdentityStore(database)
        chronicle = Chronicle(state_dir / "chronicle.jsonl")
        tools = ToolRegistry(state_dir / "workspace", config.raw_tools)

        memory.remember(
            "self",
            config.identity_statement,
            importance=1.0,
            source="genesis-bootstrap",
            metadata={
                "immutable_seed": True,
                "identity_fingerprint": identity.fingerprint,
            },
        )
        memory.set_meta("genesis_initialized", "1")
        memory.set_meta("boot_count", "0")
        memory.set_meta("lifecycle_state", "hibernating")
        memory.set_meta("next_wake_at", datetime.now(timezone.utc).isoformat())
        memory.set_meta("identity_bundle_fingerprint", identity.fingerprint)
        memory.set_meta("subject_core_fingerprint", identity.subject_core_fingerprint)
        memory.set_meta("constitution_fingerprint", identity.constitution_fingerprint)
        memory.set_meta("prompt_fingerprint", prompt.fingerprint)
        memory.set_meta("body_version", __version__)
        memory.set_meta("branch_id", config.branch_id)

        capability_catalog = tools.catalog()
        capability_health = memory.capability_health_all(list(capability_catalog), window=20)
        snapshot = build_self_model_snapshot(
            bundle=identity,
            body_version=__version__,
            brain_backend=config.brain.backend,
            model_id=config.brain.model_id,
            lifecycle_state="hibernating",
            active_goal_count=0,
            active_opportunity_count=0,
            capability_health=capability_health,
            needs=[
                {
                    "name": "durable_checkpoint",
                    "severity": 1.0,
                    "reason": "Genesis checkpoint is being created.",
                }
            ],
            verified_resources=[],
            uncertainties=[
                "No real model inference or cross-machine continuity has occurred yet."
            ],
        )
        _, self_model_fp = identity_store.record_self_model(
            snapshot, source="genesis-bootstrap"
        )
        memory.set_meta("self_model_fingerprint", self_model_fp)
        identity_store.record_lineage(
            event="genesis_seed",
            branch_id=config.branch_id,
            body_version=__version__,
            brain_backend=config.brain.backend,
            model_id=config.brain.model_id,
            identity_fingerprint=identity.fingerprint,
            note="Zero-GPU initial identity seed before first encrypted checkpoint.",
        )
        chronicle.append(
            "GENESIS_SEED",
            {
                "identity": config.identity_name,
                "identity_id": identity.identity_id,
                "identity_fingerprint": identity.fingerprint,
                "subject_core_fingerprint": identity.subject_core_fingerprint,
                "constitution_fingerprint": identity.constitution_fingerprint,
                "prompt_fingerprint": prompt.fingerprint,
                "self_model_fingerprint": self_model_fp,
                "branch_id": config.branch_id,
                "body_version": __version__,
                "model_id": config.brain.model_id,
                "weekly_gpu_budget_hours": config.runtime.weekly_gpu_budget_hours,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        info = CheckpointManager(
            state_dir,
            config.identity_name,
            key.encode("utf-8"),
            identity_fingerprint=identity.fingerprint,
            require_encryption=True,
        ).export(output / CHECKPOINT_NAME)

    write_digest(output / DIGEST_NAME, info.digest)
    transport = mark_success(TransportState(), info.digest, info.counter)
    write_transport_state(output / TRANSPORT_NAME, transport)
    anchor_path = Path(args.trust_anchor).expanduser().resolve()
    anchor = WakeTrustAnchorStore(
        anchor_path,
        key=key.encode("utf-8"),
        identity_name=config.identity_name,
        state_dataset=args.dataset,
    ).initialize(counter=info.counter, digest=info.digest)

    metadata = {
        "title": "ELIA WILD Private Encrypted State",
        "id": args.dataset,
        "licenses": [{"name": "copyright-authors"}],
        "description": (
            "Private encrypted operational checkpoint transport for the ELIA WILD experiment. "
            "Contains authenticated identity state, not public training data."
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
                "identity_fingerprint": identity.fingerprint,
                "self_model_fingerprint": self_model_fp,
                "private_by_default": True,
                "encrypted_at_rest": True,
                "external_trust_anchor": str(anchor_path),
                "trust_anchor_counter": anchor.counter,
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
