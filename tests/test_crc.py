from __future__ import annotations

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
