from __future__ import annotations

from pathlib import Path
import sys

import pytest

from elia.body.process import BoundedProcessRunner


def test_process_runner_executes_only_allowlisted_program_and_no_shell(tmp_path: Path) -> None:
    runner = BoundedProcessRunner(
        tmp_path,
        {
            "enabled": True,
            "executables": {"python": sys.executable},
            "timeout_seconds": 5,
            "max_timeout_seconds": 5,
        },
    )
    result = runner.run(
        {
            "executable": "python",
            "argv": ["-c", "import sys; print(sys.argv[1])", "$(echo injected)"],
        }
    )
    assert result.ok is True
    assert result.data["stdout"].strip() == "$(echo injected)"

    denied = runner.run({"executable": "sh", "argv": ["-c", "echo nope"]})
    assert denied.ok is False
    assert "allow-listed" in (denied.error or "")


def test_process_runner_rejects_workspace_escape_and_times_out(tmp_path: Path) -> None:
    runner = BoundedProcessRunner(
        tmp_path,
        {
            "enabled": True,
            "executables": {"python": sys.executable},
            "timeout_seconds": 0.2,
            "max_timeout_seconds": 0.3,
        },
    )
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
