from __future__ import annotations

from pathlib import Path
import sys

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
    escaped = runner.run({"executable": "python", "argv": ["-c", "print('x')"], "cwd": "../outside"})
    assert escaped.ok is False or False
