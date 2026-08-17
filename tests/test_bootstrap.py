from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from elia.checkpoint import CheckpointManager
from elia.config import load_config
from elia.wake_transport import locate_state_bundle, read_digest, read_transport_state


KEY = "bootstrap-test-secret-key-32bytes!!"


def test_bootstrap_creates_authenticated_private_state_bundle(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    output = tmp_path / "state-bundle"
    env = os.environ.copy()
    env["ELIA_CHECKPOINT_KEY"] = KEY

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "bootstrap_kaggle_state.py"),
            "--config",
            str(repo_root / "config" / "genesis.yaml"),
            "--dataset",
            "example-owner/elia-wild-state",
            "--output",
            str(output),
        ],
        cwd=repo_root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 0, result.stdout

    checkpoint, digest_file, transport_file = locate_state_bundle(output)
    assert transport_file is not None
    digest = read_digest(digest_file)
    transport = read_transport_state(transport_file)
    assert transport.last_success_digest == digest
    assert transport.last_success_counter == 1
    assert transport.pending_launch_nonce is None

    config = load_config(repo_root / "config" / "genesis.yaml")
    info = CheckpointManager(tmp_path / "inspect", config.identity_name, KEY.encode()).inspect(
        checkpoint,
        expected_digest=digest,
    )
    assert info.counter == 1
    assert info.digest == digest

    metadata = (output / "dataset-metadata.json").read_text(encoding="utf-8")
    assert '"id": "example-owner/elia-wild-state"' in metadata
    assert '"copyright-authors"' in metadata
