from __future__ import annotations

from pathlib import Path

from elia.chronicle import Chronicle
from elia.crc import compare_crc


def base_record() -> dict:
    return {
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
        "goal_fingerprints": ["g1", "g2"],
        "declared_capabilities": ["noop", "http_get"],
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
