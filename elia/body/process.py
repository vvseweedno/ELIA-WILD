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
    """Run explicitly configured executables behind an explicit sandbox boundary.

    An executable allow-list is not a filesystem/network sandbox. Production process
    execution therefore requires a configured sandbox command. Direct execution exists
    only as an explicit unsafe development/test escape hatch.
    """

    MAX_ARGS = 64
    MAX_ARG_CHARS = 4096
    MAX_STDIN_BYTES = 256_000
    MAX_OUTPUT_BYTES = 512_000

    def __init__(self, workspace: Path, config: dict[str, Any] | None = None):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.config = dict(config or {})

    def _sandbox_prefix(self) -> list[str]:
        raw = self.config.get("sandbox_command") or []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            raise ValueError("sandbox_command must be a list of argv elements")
        result: list[str] = []
        for value in raw:
            item = str(value)
            if not item or "\x00" in item or len(item) > self.MAX_ARG_CHARS:
                raise ValueError("invalid sandbox command element")
            result.append(item)
        if result and not Path(result[0]).is_absolute():
            raise ValueError("sandbox command executable must be an absolute path")
        return result

    @property
    def isolation_ready(self) -> bool:
        try:
            prefix = self._sandbox_prefix()
        except ValueError:
            return False
        if prefix:
            return Path(prefix[0]).is_file()
        return bool(self.config.get("unsafe_allow_unisolated", False))

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False)) and bool(self.executables()) and self.isolation_ready

    def executables(self) -> dict[str, str]:
        raw = self.config.get("executables") or {}
        if not isinstance(raw, dict):
            return {}
        result: dict[str, str] = {}
        for alias, raw_path in raw.items():
            alias_text = str(alias).strip()[:64]
            path = Path(str(raw_path)).expanduser()
            if not alias_text or not path.is_absolute():
                continue
            resolved = path.resolve()
            if not resolved.is_file():
                continue
            result[alias_text] = str(resolved)
        return result

    def capabilities(self) -> list[BodyCapability]:
        configured = bool(self.config.get("enabled", False))
        if not configured:
            readiness = "disabled"
        elif not self.executables():
            readiness = "no_absolute_executables"
        elif not self.isolation_ready:
            readiness = "sandbox_required"
        elif self._sandbox_prefix():
            readiness = "ready_sandboxed"
        else:
            readiness = "unsafe_unisolated_dev_mode"
        return [
            BodyCapability(
                name="process_run",
                description="Run one explicitly allow-listed executable inside ELIA's workspace through the configured sandbox boundary.",
                args="{executable: alias, argv?: [str], cwd?: str, stdin?: str, timeout_seconds?: number}",
                authority="configured_local_process",
                side_effects="child process may modify only what the configured sandbox permits",
                network_scope="defined_by_configured_sandbox",
                cost_class="local_compute",
                enabled=self.enabled,
                readiness=readiness,
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

    def _minimal_env(self) -> dict[str, str]:
        temp_root = self.workspace / ".tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        env = {
            "HOME": str(self.workspace),
            "TMPDIR": str(temp_root),
            "TEMP": str(temp_root),
            "TMP": str(temp_root),
            "PATH": str(self.config.get("path", "/usr/local/bin:/usr/bin:/bin")),
        }
        for name in ("LANG", "LC_ALL"):
            if name in os.environ:
                env[name] = os.environ[name]
        return env

    def run(self, args: dict[str, Any]) -> BodyResult:
        if not self.enabled:
            readiness = self.capabilities()[0].readiness
            return BodyResult(False, "process_run", error=f"process body is disabled/unavailable: {readiness}")
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

        sandbox = self._sandbox_prefix()
        command = [*sandbox, executable, *argv] if sandbox else [executable, *argv]
        started = time.monotonic()
        timed_out = False
        with tempfile.SpooledTemporaryFile(max_size=1_000_000) as stdout_file, tempfile.SpooledTemporaryFile(max_size=1_000_000) as stderr_file:
            popen_kwargs: dict[str, Any] = {
                "args": command,
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
                "sandboxed": bool(sandbox),
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
