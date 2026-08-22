from __future__ import annotations

from pathlib import Path
import math
import sys

import pytest

from elia.body.process import BoundedProcessRunner


def _unsafe_test_config() -> dict:
    return {
        "enabled": True,
        "executables": {"python": sys.executable},
        "unsafe_allow_unisolated": True,
        "deployment_mode": "test",
        "timeout_seconds": 5,
        "max_timeout_seconds": 5,
    }


def test_process_runner_requires_isolation_contract(tmp_path: Path) -> None:
    runner = BoundedProcessRunner(
        tmp_path,
        {"enabled": True, "executables": {"python": sys.executable}},
    )
    assert runner.enabled is False
    capability = runner.capabilities()[0]
    assert capability.readiness == "sandbox_required"
    result = runner.run({"executable": "python", "argv": ["-c", "print('nope')"]})
    assert result.ok is False
    assert "sandbox_required" in (result.error or "")


def test_existing_executable_is_not_itself_a_sandbox_attestation(tmp_path: Path) -> None:
    runner = BoundedProcessRunner(
        tmp_path,
        {
            "enabled": True,
            "executables": {"python": sys.executable},
            "sandbox_command": [sys.executable, "--"],
            "sandbox_profile": {
                "mechanism": "bubblewrap",
                "filesystem_scope": "workspace",
                "network_scope": "none",
            },
        },
    )
    assert runner.enabled is False
    assert runner.capabilities()[0].readiness == "sandbox_required"


def test_process_runner_executes_only_allowlisted_program_and_no_shell_in_explicit_dev_mode(tmp_path: Path) -> None:
    runner = BoundedProcessRunner(tmp_path, _unsafe_test_config())
    result = runner.run(
        {
            "executable": "python",
            "argv": ["-c", "import os,sys; print(sys.argv[1]); print(os.environ['HOME'])", "$(echo injected)"],
        }
    )
    assert result.ok is True
    lines = result.data["stdout"].strip().splitlines()
    assert lines[0] == "$(echo injected)"
    assert Path(lines[1]).resolve() == tmp_path.resolve()
    assert result.data["sandboxed"] is False

    denied = runner.run({"executable": "sh", "argv": ["-c", "echo nope"]})
    assert denied.ok is False
    assert "allow-listed" in (denied.error or "")


def test_process_runner_rejects_workspace_escape_and_times_out(tmp_path: Path) -> None:
    config = _unsafe_test_config()
    config.update({"timeout_seconds": 0.2, "max_timeout_seconds": 0.3})
    runner = BoundedProcessRunner(tmp_path, config)
    with pytest.raises(ValueError, match="escapes workspace"):
        runner.run(
            {
                "executable": "python",
                "argv": ["-c", "print('x')"],
                "cwd": "../outside",
            }
        )

    timed = runner.run(
        {
            "executable": "python",
            "argv": ["-c", "import time; time.sleep(2)"],
            "timeout_seconds": 0.1,
        }
    )
    assert timed.ok is False
    assert timed.data["timed_out"] is True
    assert timed.data["returncode"] is not None
    assert "timed out" in (timed.error or "")


def test_process_runner_rejects_symlink_cwd_before_execution(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    runner = BoundedProcessRunner(tmp_path, _unsafe_test_config())

    with pytest.raises(OSError):
        runner.run(
            {
                "executable": "python",
                "argv": ["-c", "open('escaped.txt', 'w').write('bad')"],
                "cwd": "linked",
            }
        )
    assert not (outside / "escaped.txt").exists()


def test_process_runner_kills_on_aggregate_output_bound(tmp_path: Path) -> None:
    runner = BoundedProcessRunner(tmp_path, _unsafe_test_config())
    result = runner.run(
        {
            "executable": "python",
            "argv": ["-c", "import sys; sys.stdout.write('x' * 700000); sys.stdout.flush()"],
        }
    )
    assert result.ok is False
    assert result.data["output_limited"] is True
    assert len(result.data["stdout"].encode("utf-8")) <= runner.MAX_OUTPUT_BYTES
    assert "bounded limit" in (result.error or "")


def test_process_runner_uses_one_locked_budget_across_stdout_and_stderr(tmp_path: Path) -> None:
    runner = BoundedProcessRunner(tmp_path, _unsafe_test_config())
    result = runner.run(
        {
            "executable": "python",
            "argv": [
                "-c",
                (
                    "import sys,threading; "
                    "threads=[threading.Thread(target=lambda s,c: "
                    "(s.write(c*400000),s.flush()), args=(stream,char)) "
                    "for stream,char in ((sys.stdout,'o'),(sys.stderr,'e'))]; "
                    "[t.start() for t in threads]; [t.join() for t in threads]"
                ),
            ],
        }
    )
    total = len(result.data["stdout"].encode()) + len(result.data["stderr"].encode())
    assert result.ok is False
    assert result.data["output_limited"] is True
    assert total <= runner.MAX_OUTPUT_BYTES
    assert result.data["stdout_truncated"] or result.data["stderr_truncated"]


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -1.0, 0.0, True, "1"])
def test_process_runner_rejects_invalid_timeout_before_execution(
    tmp_path: Path, invalid: object
) -> None:
    marker = tmp_path / "executed.txt"
    runner = BoundedProcessRunner(tmp_path, _unsafe_test_config())
    result = runner.run(
        {
            "executable": "python",
            "argv": ["-c", "from pathlib import Path; Path('executed.txt').write_text('bad')"],
            "timeout_seconds": invalid,
        }
    )
    assert result.ok is False
    assert "finite positive" in (result.error or "")
    assert not marker.exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["--share-net"],
        ["--dev-bind", "/", "/dev"],
        ["--bind", "/", "/"],
        ["--bind", "/tmp", "/tmp"],
        ["--unshare-all", "operand-that-must-not-count-as-a-flag"],
    ],
)
def test_process_runner_rejects_contradictory_or_broad_bwrap_prefix(
    tmp_path: Path, extra: list[str]
) -> None:
    workspace = str(tmp_path.resolve())
    prefix = [
        sys.executable,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--bind",
        workspace,
        workspace,
        *extra,
        "--",
    ]
    runner = BoundedProcessRunner(
        tmp_path,
        {
            "enabled": True,
            "executables": {"python": sys.executable},
            "sandbox_command": prefix,
            "sandbox_profile": {
                "mechanism": "bubblewrap",
                "filesystem_scope": "workspace",
                "network_scope": "none",
            },
        },
    )
    assert runner.enabled is False
    assert runner.capabilities()[0].readiness == "sandbox_required"


def test_unsafe_unisolated_mode_never_claims_production_readiness(tmp_path: Path) -> None:
    config = _unsafe_test_config()
    config["deployment_mode"] = "production"
    runner = BoundedProcessRunner(tmp_path, config)
    assert runner.enabled is False
    assert runner.capabilities()[0].readiness == "unsafe_unisolated_requires_dev_mode"
