from __future__ import annotations

import json
import multiprocessing
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


def _advance_anchor(path: str, digest: str, queue) -> None:
    try:
        accepted = store(Path(path)).advance(counter=2, digest=digest)
        queue.put(("ok", accepted.digest))
    except Exception as exc:  # pragma: no cover - asserted via child result.
        queue.put(("error", type(exc).__name__))


def test_anchor_requires_explicit_trusted_initialization(tmp_path: Path) -> None:
    anchor = store(tmp_path / "anchor.json")
    with pytest.raises(WakeTrustAnchorError, match="missing"):
        anchor.verify(counter=1, digest="a" * 64)

    initialized = anchor.initialize(counter=1, digest="a" * 64)
    assert initialized.counter == 1
    assert initialized.digest == "a" * 64
    assert anchor.verify(counter=1, digest="a" * 64) == initialized


def test_anchor_rejects_rollback_and_same_counter_fork(tmp_path: Path) -> None:
    anchor = store(tmp_path / "anchor.json")
    anchor.initialize(counter=3, digest="c" * 64)

    with pytest.raises(WakeTrustAnchorRollbackError, match="rollback"):
        anchor.verify(counter=2, digest="b" * 64)
    with pytest.raises(WakeTrustAnchorRollbackError, match="fork/replay"):
        anchor.verify(counter=3, digest="d" * 64)

    current = anchor.read()
    assert current is not None
    assert current.counter == 3
    assert current.digest == "c" * 64


def test_source_verification_never_learns_a_newer_state_from_dataset(tmp_path: Path) -> None:
    anchor = store(tmp_path / "anchor.json")
    anchor.initialize(counter=2, digest="b" * 64)

    with pytest.raises(WakeTrustAnchorError, match="ahead of the durable"):
        anchor.verify(counter=3, digest="c" * 64)

    current = anchor.read()
    assert current is not None
    assert current.counter == 2
    assert current.digest == "b" * 64


def test_anchor_advances_only_through_explicit_trusted_forward_acceptance(
    tmp_path: Path,
) -> None:
    anchor = store(tmp_path / "anchor.json")
    anchor.initialize(counter=1, digest="a" * 64)
    advanced = anchor.advance(counter=2, digest="b" * 64)
    assert advanced.counter == 2
    assert anchor.verify(counter=2, digest="b" * 64) == advanced
    assert anchor.accept(counter=2, digest="b" * 64) == advanced


def test_anchor_rejects_generation_gap(tmp_path: Path) -> None:
    anchor = store(tmp_path / "anchor.json")
    anchor.initialize(counter=1, digest="a" * 64)
    with pytest.raises(WakeTrustAnchorError, match="one authenticated generation"):
        anchor.advance(counter=3, digest="c" * 64)


def test_concurrent_anchor_advance_is_compare_and_swap(tmp_path: Path) -> None:
    path = tmp_path / "anchor.json"
    store(path).initialize(counter=1, digest="a" * 64)
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    processes = [
        context.Process(target=_advance_anchor, args=(str(path), digest, queue))
        for digest in ("b" * 64, "c" * 64)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=3)
        assert process.exitcode == 0
    results = [queue.get(timeout=1), queue.get(timeout=1)]
    assert sorted(item[0] for item in results) == ["error", "ok"]
    current = store(path).read()
    assert current is not None
    assert current.counter == 2
    assert current.digest == next(item[1] for item in results if item[0] == "ok")


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
