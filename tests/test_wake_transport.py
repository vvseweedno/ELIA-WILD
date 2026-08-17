from __future__ import annotations

import json
from pathlib import Path

import pytest

from elia.wake_transport import (
    CHECKPOINT_NAME,
    DIGEST_NAME,
    TRANSPORT_NAME,
    TransportState,
    build_kernel_metadata,
    launch_suppressed,
    locate_state_bundle,
    mark_failure,
    mark_pending,
    mark_success,
    parse_dataset_status,
    parse_kernel_status,
    read_digest,
    read_transport_state,
    render_runner,
    validate_digest,
    validate_relay_report,
    write_digest,
    write_transport_state,
)


D1 = "a" * 64
D2 = "b" * 64


def test_digest_validation_and_roundtrip(tmp_path: Path) -> None:
    assert validate_digest(D1.upper()) == D1
    path = tmp_path / DIGEST_NAME
    write_digest(path, D1)
    assert read_digest(path) == D1
    with pytest.raises(ValueError):
        validate_digest("not-a-digest")


def test_transport_state_failure_suppression_and_success_reset(tmp_path: Path) -> None:
    state = TransportState()
    pending = mark_pending(state, "nonce-1")
    assert pending.pending_launch_nonce == "nonce-1"
    assert pending.pending_since is not None

    failed = pending
    for index in range(3):
        failed = mark_failure(failed, f"failure-{index}")
    assert failed.consecutive_kernel_failures == 3
    assert launch_suppressed(failed) is True

    success = mark_success(failed, D2, 9)
    assert success.consecutive_kernel_failures == 0
    assert success.last_success_digest == D2
    assert success.last_success_counter == 9
    assert launch_suppressed(success) is False

    path = tmp_path / TRANSPORT_NAME
    write_transport_state(path, success)
    loaded = read_transport_state(path)
    assert loaded.as_dict() == success.as_dict()


def test_locate_state_bundle_requires_unique_checkpoint_and_digest(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / CHECKPOINT_NAME).write_bytes(b"checkpoint")
    write_digest(nested / DIGEST_NAME, D1)
    write_transport_state(nested / TRANSPORT_NAME, TransportState())

    checkpoint, digest, transport = locate_state_bundle(tmp_path)
    assert checkpoint == nested / CHECKPOINT_NAME
    assert digest == nested / DIGEST_NAME
    assert transport == nested / TRANSPORT_NAME

    (tmp_path / CHECKPOINT_NAME).write_bytes(b"duplicate")
    with pytest.raises(FileNotFoundError, match="exactly one"):
        locate_state_bundle(tmp_path)


def test_kernel_status_parser_is_tolerant_but_conservative() -> None:
    assert parse_kernel_status("Kernel status: RUNNING") == "running"
    assert parse_kernel_status("status = queued") == "queued"
    assert parse_kernel_status("KernelWorkerStatus.COMPLETE") == "complete"
    assert parse_kernel_status("latest run failed with error") == "failed"
    assert parse_kernel_status("something undocumented") == "unknown"
    assert parse_kernel_status("") == "unknown"


def test_dataset_status_parser_prefers_structured_state() -> None:
    assert parse_dataset_status('{"status": "READY"}') == "ready"
    assert parse_dataset_status('{"dataset": {"status": "PENDING"}}') == "pending"
    assert parse_dataset_status('{"state": "FAILED", "message": "upload error"}') == "failed"
    assert parse_dataset_status("Dataset status: processing") == "pending"
    assert parse_dataset_status("Dataset is complete") == "ready"
    assert parse_dataset_status("undocumented response") == "unknown"


def test_kernel_metadata_defaults_to_t4_and_private_state() -> None:
    metadata = build_kernel_metadata(
        kernel_id="owner/elia-wild-genesis",
        state_dataset="owner/elia-wild-state",
    )
    assert metadata["machine_shape"] == "NvidiaTeslaT4"
    assert metadata["is_private"] == "true"
    assert metadata["enable_gpu"] == "true"
    assert metadata["enable_internet"] == "true"
    assert metadata["dataset_sources"] == ["owner/elia-wild-state"]


def test_runner_render_injects_only_nonsecret_config() -> None:
    template = "prefix\nWAKE_CONFIG = __ELIA_WAKE_CONFIG__\nsuffix\n"
    rendered = render_runner(
        template,
        {
            "launch_nonce": "nonce",
            "source_digest": D1,
            "repo_ref": "abc123",
        },
    )
    assert "__ELIA_WAKE_CONFIG__" not in rendered
    assert '"launch_nonce": "nonce"' in rendered
    assert "ELIA_CHECKPOINT_KEY" not in rendered


def test_real_runner_template_renders_to_valid_python() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    template = (repo_root / "runtime" / "kaggle" / "runner_template.py").read_text(encoding="utf-8")
    rendered = render_runner(
        template,
        {
            "version": 1,
            "launch_nonce": "nonce-compile-test",
            "source_digest": D1,
            "repo_url": "https://github.com/vvseweedno/ELIA-WILD.git",
            "repo_ref": "deadbeef",
            "max_cycles": 8,
        },
    )
    compile(rendered, "elia_wild_runner.py", "exec")
    assert "__ELIA_WAKE_CONFIG__" not in rendered
    assert D1 in rendered
    assert "bootstrap-test-secret" not in rendered


def test_relay_report_requires_nonce_source_digest_and_valid_output() -> None:
    report = {
        "launch_nonce": "nonce-1",
        "source_digest": D1,
        "output_digest": D2,
        "output_counter": 5,
    }
    digest, counter = validate_relay_report(
        report,
        expected_nonce="nonce-1",
        expected_source_digest=D1,
    )
    assert digest == D2
    assert counter == 5

    with pytest.raises(ValueError, match="nonce"):
        validate_relay_report(report, expected_nonce="wrong", expected_source_digest=D1)
    with pytest.raises(ValueError, match="source digest"):
        validate_relay_report(report, expected_nonce="nonce-1", expected_source_digest=D2)


def test_missing_transport_file_is_clean_bootstrap_state(tmp_path: Path) -> None:
    state = read_transport_state(tmp_path / TRANSPORT_NAME)
    assert state == TransportState()


def test_transport_file_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / TRANSPORT_NAME
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        read_transport_state(path)
