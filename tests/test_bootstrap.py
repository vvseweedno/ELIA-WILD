from __future__ import annotations

import base64
import os
from pathlib import Path
import subprocess
import sys

from elia.checkpoint import CheckpointManager, ENVELOPE_MAGIC
from elia.config import load_config
from elia.wake_anchor import WakeTrustAnchorStore
from elia.wake_transport import locate_state_bundle, read_digest, read_transport_state


KEY = "bootstrap-test-secret-key-32bytes!!"
ENC_KEY = b"k" * 32


def test_bootstrap_creates_authenticated_encrypted_private_state_bundle(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    output = tmp_path / "state-bundle"
    anchor_path = tmp_path / "relay-host" / "trust-anchor.json"
    env = os.environ.copy()
    env["ELIA_CHECKPOINT_KEY"] = KEY
    env["ELIA_CHECKPOINT_ENCRYPTION_KEY"] = base64.b64encode(ENC_KEY).decode("ascii")

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
            "--trust-anchor",
            str(anchor_path),
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
    assert checkpoint.read_bytes().startswith(ENVELOPE_MAGIC)
    assert transport_file is not None
    digest = read_digest(digest_file)
    transport = read_transport_state(
        transport_file,
        key=KEY,
        require_auth=True,
    )
    assert transport.last_success_digest == digest
    assert transport.last_success_counter == 1
    assert transport.pending_launch_nonce is None

    config = load_config(repo_root / "config" / "genesis.yaml")
    info = CheckpointManager(
        tmp_path / "inspect",
        config.identity_name,
        KEY.encode(),
        encryption_key=ENC_KEY,
        require_encryption=True,
    ).inspect(
        checkpoint,
        expected_digest=digest,
    )
    assert info.counter == 1
    assert info.digest == digest

    anchor = WakeTrustAnchorStore(
        anchor_path,
        key=KEY.encode(),
        identity_name=config.identity_name,
        state_dataset="example-owner/elia-wild-state",
    ).read()
    assert anchor is not None
    assert anchor.counter == info.counter
    assert anchor.digest == info.digest

    metadata = (output / "dataset-metadata.json").read_text(encoding="utf-8")
    assert '"id": "example-owner/elia-wild-state"' in metadata
    assert '"copyright-authors"' in metadata
    assert "Private Encrypted State" in metadata
