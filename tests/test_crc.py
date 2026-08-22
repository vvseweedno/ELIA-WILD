from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from elia.chronicle import Chronicle
from elia.crc import compare_crc, read_crc
from elia.identity import IdentityStore


def base_record() -> dict:
    return {
        "schema_version": 2,
        "created_at": "2026-08-18T00:00:00+00:00",
        "identity_id": "elia-wild",
        "identity_fingerprint": "a" * 64,
        "subject_core_fingerprint": "b" * 64,
        "constitution_fingerprint": "c" * 64,
        "prompt_fingerprint": "d" * 64,
        "branch_id": "main",
        "body_version": "1.0.0a1",
        "brain_backend": "transformers_4bit",
        "model_id": "Qwen/A",
        "chronicle_valid": True,
        "chronicle_seq": 10,
        "chronicle_hash": "e" * 64,
        "checkpoint_digest": None,
        "checkpoint_counter": 0,
        "self_model_fingerprint": None,
        "lineage_event_count": 0,
        "lineage_head_id": None,
        "lineage_head_hash": None,
        "lineage_valid": True,
        "goal_fingerprints": ["1" * 64, "2" * 64],
        "active_goal_count": 2,
        "active_opportunity_count": 0,
        "declared_capabilities": ["noop", "http_get"],
        "available_skills": [],
        "verified_resource_fingerprint": "f" * 64,
    }


def test_crc_model_swap_can_remain_continuous() -> None:
    left = base_record()
    right = dict(left)
    right["model_id"] = "Qwen/B"
    right["brain_backend"] = "openai_compatible"
    right["chronicle_seq"] = 20
    comparison = compare_crc(left, right)
    assert comparison.status == "continuous"
    assert not comparison.critical_failures
    assert "model_id" in comparison.changed
    assert "chronicle_monotonicity_unproven" in comparison.preserved


def test_crc_body_prompt_upgrade_and_capability_growth_can_remain_continuous() -> None:
    left = base_record()
    right = dict(left)
    right["body_version"] = "1.1.0a1"
    right["prompt_fingerprint"] = "e" * 64
    right["chronicle_seq"] = 20
    right["declared_capabilities"] = [
        "noop",
        "http_get",
        "world_model_query",
        "browser_navigate",
        "mcp_discover",
    ]
    comparison = compare_crc(left, right)
    assert comparison.status == "continuous"
    assert comparison.score >= 0.80
    assert not comparison.critical_failures
    assert "body_version" in comparison.changed
    assert "prompt_fingerprint" in comparison.changed
    assert "capability_superset" in comparison.preserved


def test_crc_subject_core_change_breaks_continuity() -> None:
    left = base_record()
    right = dict(left)
    right["identity_fingerprint"] = "e" * 64
    right["subject_core_fingerprint"] = "f" * 64
    right["chronicle_seq"] = 20
    comparison = compare_crc(left, right)
    assert comparison.status == "broken"
    assert any("identity_fingerprint" in item for item in comparison.critical_failures)
    assert any("subject_core_fingerprint" in item for item in comparison.critical_failures)


def test_crc_backward_chronicle_is_break() -> None:
    left = base_record()
    right = dict(left)
    right["chronicle_seq"] = 5
    comparison = compare_crc(left, right)
    assert comparison.status == "broken"
    assert "Chronicle sequence moved backward" in comparison.critical_failures


def test_crc_same_counter_digest_rewrite_is_a_hard_failure() -> None:
    left = base_record()
    right = dict(left)
    left["checkpoint_counter"] = 7
    right["checkpoint_counter"] = 7
    left["checkpoint_digest"] = "3" * 64
    right["checkpoint_digest"] = "4" * 64

    comparison = compare_crc(left, right)

    assert comparison.status == "broken"
    assert "checkpoint digest changed at an unchanged counter" in comparison.critical_failures


def test_crc_rejects_missing_equal_identity_fields_instead_of_preserving_none() -> None:
    left = base_record()
    right = base_record()
    del left["identity_fingerprint"]
    del right["identity_fingerprint"]

    comparison = compare_crc(left, right)

    assert comparison.status == "broken"
    assert any("identity_fingerprint" in item for item in comparison.critical_failures)


def test_read_crc_rejects_payload_tampering(tmp_path: Path) -> None:
    payload = base_record()
    # A syntactically valid but unauthenticated capsule must never become a baseline.
    payload["capsule_fingerprint"] = "0" * 64
    path = tmp_path / "crc.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        read_crc(path)


def test_read_crc_rejects_nonfinite_extension_before_fingerprint_check(
    tmp_path: Path,
) -> None:
    payload = base_record()
    payload["untrusted_extension"] = float("nan")
    payload["capsule_fingerprint"] = "0" * 64
    path = tmp_path / "crc.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="strict JSON rejects non-finite"):
        read_crc(path)


def test_read_crc_accepts_one_legacy_schema_cycle_with_its_original_checksum(
    tmp_path: Path,
) -> None:
    payload = base_record()
    payload["schema_version"] = 1
    del payload["lineage_head_hash"]
    del payload["lineage_valid"]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    payload["capsule_fingerprint"] = sha256(canonical.encode("utf-8")).hexdigest()
    path = tmp_path / "legacy-crc.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = read_crc(path)
    current = base_record()
    comparison = compare_crc(restored, current)

    assert restored["schema_version"] == 1
    assert comparison.status == "continuous"
    assert "schema_version" in comparison.changed
    assert any("schema upgraded" in warning for warning in comparison.warnings)


def _append_lineage(store: IdentityStore, note: str) -> None:
    store.record_lineage(
        event="cycle",
        branch_id="main",
        body_version="1",
        brain_backend="mock",
        model_id="mock",
        identity_fingerprint="a" * 64,
        note=note,
    )


def test_crc_requires_exact_identity_lineage_prefix_when_requested(
    tmp_path: Path,
) -> None:
    store = IdentityStore(tmp_path / "memory.sqlite3")
    _append_lineage(store, "accepted")
    first = store.lineage(None)[-1]
    left = base_record()
    left.update(
        lineage_event_count=1,
        lineage_head_id=first.id,
        lineage_head_hash=first.event_hash,
    )
    _append_lineage(store, "successor")
    events = store.lineage(None)
    right = base_record()
    right.update(
        lineage_event_count=2,
        lineage_head_id=events[-1].id,
        lineage_head_hash=events[-1].event_hash,
    )

    comparison = compare_crc(
        left,
        right,
        lineage_store=store,
        require_lineage_ancestry=True,
    )

    assert comparison.status == "continuous"
    assert "lineage_prefix_ancestry" in comparison.preserved

    forged_left = dict(left)
    forged_left["lineage_head_hash"] = "9" * 64
    broken = compare_crc(
        forged_left,
        right,
        lineage_store=store,
        require_lineage_ancestry=True,
    )
    assert broken.status == "broken"
    assert any("lineage prefix ancestry failed" in item for item in broken.critical_failures)


def _record_for_head(seq: int, digest: str) -> dict:
    item = base_record()
    item["chronicle_seq"] = seq
    item["chronicle_hash"] = digest
    return item


def test_strict_crc_proves_valid_chronicle_continuation(tmp_path: Path) -> None:
    chronicle = Chronicle(tmp_path / "chronicle.jsonl")
    chronicle.append("GENESIS", {"seed": 1})
    left_seq, left_hash = chronicle.head()
    left = _record_for_head(left_seq, left_hash)

    chronicle.append("CYCLE", {"step": 2})
    right_seq, right_hash = chronicle.head()
    right = _record_for_head(right_seq, right_hash)
    comparison = compare_crc(
        left,
        right,
        chronicle=chronicle,
        require_ancestry=True,
    )
    assert comparison.status == "continuous"
    assert not comparison.critical_failures
    assert "chronicle_prefix_ancestry" in comparison.preserved


def test_strict_crc_rejects_rewritten_valid_history_of_equal_or_greater_length(
    tmp_path: Path,
) -> None:
    path = tmp_path / "chronicle.jsonl"
    original = Chronicle(path)
    original.append("GENESIS", {"seed": "original"})
    original.append("CYCLE", {"step": "accepted-past"})
    left_seq, left_hash = original.head()
    left = _record_for_head(left_seq, left_hash)

    # Replace the entire file with a different, internally valid chain whose length
    # is not lower. Sequence monotonicity alone cannot distinguish this substitution.
    path.unlink()
    replacement = Chronicle(path)
    replacement.append("GENESIS", {"seed": "replacement"})
    replacement.append("CYCLE", {"step": "different-past"})
    replacement.append("CYCLE", {"step": "extra-future"})
    assert replacement.verify() == (True, None)
    right_seq, right_hash = replacement.head()
    right = _record_for_head(right_seq, right_hash)

    comparison = compare_crc(
        left,
        right,
        chronicle=replacement,
        require_ancestry=True,
    )
    assert comparison.status == "broken"
    assert any(
        "Chronicle prefix ancestry failed" in item
        for item in comparison.critical_failures
    )
