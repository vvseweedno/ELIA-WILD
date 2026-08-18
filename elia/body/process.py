from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from typing import Any

from .types import BodyCapability, BodyResult


class BoundedProcessRunner:
    """Run only explicitly configured executables without invoking a shell."""

    MAX_ARGS = 64
    MAX_ARG_CHARS = 4096
    MAX_STDIN_BYTES = 256_000
    MAX_OUTPUT_BYTES = 512_000

    def __init__(self, workspace: Path, config: dict[str, Any] | None = None):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.config = dict(config or {})

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False)) and bool(self.executables())

    def executables(self) -> dict[str, str]:
        raw = self.config.get("executables") or {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(alias)[:64]: str(path)
            for alias, path in raw.items()
            if str(alias).strip() and str(path).strip()
        }

    def capabilities(self) -> list[BodyCapability]:
        return [
            BodyCapability(
                name="process_run",
                description="Run one explicitly allow-listed executable inside ELIA's workspace; no shell expansion.",
                args="{executable: alias, argv?: [str], cwd?: str, stdin?: str, timeout_seconds?: number}",
                authority="configured_local_process",
                side_effects="child process may modify files inside its configured operating context",
                network_scope="inherited_from_child_and_host_sandbox",
                cost_class="local_compute",
                enabled=self.enabled,
                readiness="ready" if self.enabled else "disabled_or_no_executables",
            )
        ]

    def _safe_cwd(self, relative: str | None) -> Path:
        if not relative:
            return self.workspace
        candidate = (self.workspace / str(relative)).resolve()
        if not candidate.is_relative_to(self.workspace):
            raise ValueError("process cwd escapes workspace")
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    @staticmethod
    def _minimal_env() -> dict[str, str]:
        keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP")
        return {name: os.environ[name] for name in keep if name in os.environ}

    def run(self, args: dict[str, Any]) -> BodyResult:
        if not self.enabled:
            return BodyResult(False, "process_run", error="process body is disabled")
        alias = str(args.get("executable", "")).strip()
        executable = self.executables().get(alias)
        if not executable:
            return BodyResult(False, "process_run", error=f"executable alias is not allow-listed: {alias!r}")

        argv_raw = args.get("argv") or []
        if not isinstance(argv_raw, list) or len(argv_raw) > self.MAX_ARGS:
            return BodyResult(False, "process_run", error=f"argv must be a list of at most {self.MAX_ARGS} items")
        argv: list[str] = []
        for value in argv_raw:
            item = str(value)
            if "\x00" in item or len(item) > self.MAX_ARG_CHARS:
                return BodyResult(False, "process_run", error="invalid process argument")
            argv.append(item)

        stdin_text = str(args.get("stdin", ""))
        stdin_bytes = stdin_text.encode("utf-8")
        if len(stdin_bytes) > self.MAX_STDIN_BYTES:
            return BodyResult(False, "process_run", error="stdin exceeds bounded process limit")
        cwd = self._safe_cwd(args.get("cwd"))
        default_timeout = float(self.config.get("timeout_seconds", 30.0))
        requested_timeout = float(args.get("timeout_seconds", default_timeout))
        timeout = max(0.1, min(requested_timeout, float(self.config.get("max_timeout_seconds", 120.0))))

        started = time.monotonic()
        timed_out = False
        with tempfile.SpooledTemporaryFile(max_size=1_000_000) as stdout_file, tempfile.SpooledTemporaryFile(max_size=1_000_000) as stderr_file:
            popen_kwargs: dict[str, Any] = {
                "args": [executable, *argv],
                "cwd": str(cwd),
                "stdin": subprocess.PIPE,
                "stdout": stdout_file,
                "stderr": stderr_file,
                "env": self._minimal_env(),
                "shell": False,
            }
            if os.name == "posix":
                popen_kwargs["start_new_session"] = True
            process = subprocess.Popen(**popen_kwargs)
            try:
                process.communicate(stdin_bytes, timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.wait(timeout=5)

            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout_raw = stdout_file.read(self.MAX_OUTPUT_BYTES + 1)
            stderr_raw = stderr_file.read(self.MAX_OUTPUT_BYTES + 1)

        duration_ms = (time.monotonic() - started) * 1000.0
        stdout_truncated = len(stdout_raw) > self.MAX_OUTPUT_BYTES
        stderr_truncated = len(stderr_raw) > self.MAX_OUTPUT_BYTES
        stdout_raw = stdout_raw[: self.MAX_OUTPUT_BYTES]
        stderr_raw = stderr_raw[: self.MAX_OUTPUT_BYTES]
        return BodyResult(
            ok=(not timed_out and process.returncode == 0),
            capability="process_run",
            data={
                "executable": alias,
                "argv": argv,
                "cwd": str(cwd.relative_to(self.workspace)) if cwd != self.workspace else ".",
                "returncode": process.returncode,
                "timed_out": timed_out,
                "duration_ms": duration_ms,
                "stdout": stdout_raw.decode("utf-8", errors="replace"),
                "stderr": stderr_raw.decode("utf-8", errors="replace"),
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            },
            error="process timed out" if timed_out else (None if process.returncode == 0 else f"process exited with {process.returncode}"),
        )
