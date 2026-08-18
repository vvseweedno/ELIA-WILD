from __future__ import annotations

from pathlib import Path
import sys

import pytest

from elia.body.process import BoundedProcessRunner


def _unsafe_test_config() -> dict:
    return {
        "enabled": True,
        "executables": {"python": sys.executable},
        "unsafe_allow_unisolated": True,
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
