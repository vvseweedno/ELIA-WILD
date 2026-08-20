from __future__ import annotations

import json
from pathlib import Path

import pytest

from elia.wake_anchor import (
    WakeTrustAnchorError,
    WakeTrustAnchorRollbackError,
    WakeTrustAnchorStore,
)


KEY = b"wake-anchor-test-key-32-bytes!!!"
DATASET = "owner/elia-state"


def store(path: Path) -> WakeTrustAnchorStore:
    return WakeTrustAnchorStore(
        path,
        key=KEY,
        identity_name="ELIA",
        state_dataset=DATASET,
    )


def test_anchor_requires_explicit_trusted_initialization(tmp_path: Path) -> None:
    anchor = store(tmp_path / "anchor.json")
    with pytest.raises(WakeTrustAnchorError, match="missing"):
        anchor.accept(counter=1, digest="a" * 64)

    initialized = anchor.initialize(counter=1, digest="a" * 64)
    assert initialized.counter == 1
    assert initialized.digest == "a" * 64


def test_anchor_rejects_rollback_and_same_counter_fork(tmp_path: Path) -> None:
    anchor = store(tmp_path / "anchor.json")
    anchor.initialize(counter=3, digest="c" * 64)

    with pytest.raises(WakeTrustAnchorRollbackError, match="rollback"):
        anchor.accept(counter=2, digest="b" * 64)
    with pytest.raises(WakeTrustAnchorRollbackError, match="fork/replay"):
        anchor.accept(counter=3, digest="d" * 64)

    current = anchor.read()
    assert current is not None
    assert current.counter == 3
    assert current.digest == "c" * 64


def test_anchor_advances_only_to_authenticated_forward_state(tmp_path: Path) -> None:
    anchor = store(tmp_path / "anchor.json")
    anchor.initialize(counter=1, digest="a" * 64)
    advanced = anchor.accept(counter=2, digest="b" * 64)
    assert advanced.counter == 2
    assert anchor.accept(counter=2, digest="b" * 64) == advanced


def test_anchor_tampering_is_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "anchor.json"
    anchor = store(path)
    anchor.initialize(counter=4, digest="e" * 64)

    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["anchor"]["counter"] = 1
    path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(WakeTrustAnchorError, match="authentication failed"):
        anchor.read()


def test_anchor_is_bound_to_dataset_and_identity(tmp_path: Path) -> None:
    path = tmp_path / "anchor.json"
    store(path).initialize(counter=1, digest="f" * 64)

    wrong_dataset = WakeTrustAnchorStore(
        path,
        key=KEY,
        identity_name="ELIA",
        state_dataset="owner/other-state",
    )
    with pytest.raises(WakeTrustAnchorError, match="Dataset mismatch"):
        wrong_dataset.read()

    wrong_identity = WakeTrustAnchorStore(
        path,
        key=KEY,
        identity_name="OTHER",
        state_dataset=DATASET,
    )
    with pytest.raises(WakeTrustAnchorError, match="identity mismatch"):
        wrong_identity.read()
