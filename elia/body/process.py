from __future__ import annotations

from datetime import datetime, timezone
import math
import os
from pathlib import Path
import signal
import subprocess
from threading import Event, Lock, Thread
import time
from typing import Any, Callable

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
    MAX_TIMEOUT_SECONDS = 3_600.0

    def __init__(
        self,
        workspace: Path,
        config: dict[str, Any] | None = None,
        *,
        sandbox_verifier: Callable[[list[str], dict[str, Any]], bool] | None = None,
    ):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.config = dict(config or {})
        self._sandbox_verifier = sandbox_verifier

    def _sandbox_prefix(self) -> list[str]:
        raw = self.config.get("sandbox_command") or []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            raise ValueError("sandbox_command must be a list of argv elements")
        result: list[str] = []
        for value in raw:
            if not isinstance(value, str):
                raise ValueError("sandbox command elements must be strings")
            item = value
            if not item or "\x00" in item or len(item) > self.MAX_ARG_CHARS:
                raise ValueError("invalid sandbox command element")
            result.append(item)
        if result and not Path(result[0]).is_absolute():
            raise ValueError("sandbox command executable must be an absolute path")
        return result

    def _verified_sandbox_prefix(self) -> list[str]:
        prefix = self._sandbox_prefix()
        if not prefix:
            return []
        sandbox_path = Path(prefix[0]).resolve()
        if not sandbox_path.is_file():
            raise ValueError("sandbox executable does not exist")
        profile = self.config.get("sandbox_profile") or {}
        if not isinstance(profile, dict):
            raise ValueError("sandbox_profile must be an object")
        if self._sandbox_verifier is not None:
            if not bool(self._sandbox_verifier(prefix, dict(profile))):
                raise ValueError("sandbox verifier rejected the configured isolation profile")
            return prefix

        # Production readiness is intentionally limited to a command whose isolation
        # semantics can be inspected. Merely naming an executable is not an attestation.
        executable_name = sandbox_path.name
        mechanism = str(profile.get("mechanism", "")).strip().lower()
        required_profile = (
            mechanism == "bubblewrap"
            and str(profile.get("filesystem_scope", "")).strip().lower() == "workspace"
            and str(profile.get("network_scope", "")).strip().lower() == "none"
        )
        required_flags = {"--unshare-all", "--die-with-parent", "--new-session"}
        dangerous_flags = {
            "--share-net",
            "--share-user",
            "--share-pid",
            "--share-ipc",
            "--share-uts",
            "--share-cgroup",
            "--dev-bind",
            "--dev-bind-try",
            "--bind-try",
            "--cap-add",
            "--allow-setuid",
        }
        if prefix.count("--") != 1 or prefix[-1] != "--":
            raise ValueError("sandbox command must terminate with exactly one --")

        zero_argument_options = required_flags | {
            "--unshare-user",
            "--unshare-user-try",
            "--unshare-ipc",
            "--unshare-pid",
            "--unshare-net",
            "--unshare-uts",
            "--unshare-cgroup",
            "--unshare-cgroup-try",
            "--clearenv",
            "--level-prefix",
            "--disable-userns",
            "--assert-userns-disabled",
        }
        one_argument_options = {
            "--proc",
            "--dev",
            "--tmpfs",
            "--dir",
            "--chdir",
            "--remount-ro",
            "--unsetenv",
            "--hostname",
            "--uid",
            "--gid",
            "--lock-file",
            "--sync-fd",
            "--userns-block-fd",
            "--info-fd",
            "--json-status-fd",
            "--argv0",
        }
        two_argument_options = {"--bind", "--ro-bind", "--ro-bind-try", "--setenv"}
        seen_zero_argument_options: set[str] = set()
        writable_binds: list[tuple[str, str]] = []
        readonly_binds: list[tuple[str, str]] = []
        index = 1
        while index < len(prefix) - 1:
            item = prefix[index]
            if item in dangerous_flags or any(
                item.startswith(flag + "=") for flag in dangerous_flags
            ):
                raise ValueError("sandbox command contains a contradictory/escalating option")
            if item in zero_argument_options:
                seen_zero_argument_options.add(item)
                index += 1
                continue
            if item in two_argument_options:
                if index + 2 >= len(prefix) - 1:
                    raise ValueError(f"sandbox option {item} has no complete source/destination pair")
                pair = (prefix[index + 1], prefix[index + 2])
                if item == "--bind":
                    writable_binds.append(pair)
                elif item in {"--ro-bind", "--ro-bind-try"}:
                    readonly_binds.append(pair)
                index += 3
                continue
            if item in one_argument_options:
                if index + 1 >= len(prefix) - 1:
                    raise ValueError(f"sandbox option {item} has no argument")
                index += 2
                continue
            raise ValueError(f"unsupported sandbox option: {item}")
        workspace_pair = (str(self.workspace), str(self.workspace))
        binds_workspace = writable_binds == [workspace_pair]
        if any(
            not Path(source).is_absolute()
            or not Path(destination).is_absolute()
            or source == "/"
            or destination == "/"
            for source, destination in readonly_binds
        ):
            raise ValueError("sandbox command may not expose the host root even read-only")
        try:
            sandbox_metadata = sandbox_path.stat()
            trusted_executable = sandbox_metadata.st_uid == 0 and not (
                sandbox_metadata.st_mode & 0o022
            )
        except OSError:
            trusted_executable = False
        if not (
            executable_name in {"bwrap", "bubblewrap"}
            and trusted_executable
            and required_profile
            and required_flags.issubset(seen_zero_argument_options)
            and binds_workspace
        ):
            raise ValueError(
                "sandbox command lacks a verified bubblewrap workspace/no-network profile"
            )
        return prefix

    @property
    def isolation_ready(self) -> bool:
        try:
            prefix = self._verified_sandbox_prefix()
        except ValueError:
            return False
        if prefix:
            return True
        return bool(self.config.get("unsafe_allow_unisolated", False)) and str(
            self.config.get("deployment_mode", "")
        ).strip().lower() in {"development", "test"}

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
            readiness = (
                "unsafe_unisolated_requires_dev_mode"
                if bool(self.config.get("unsafe_allow_unisolated", False))
                else "sandbox_required"
            )
        elif self._verified_sandbox_prefix():
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

    def _open_safe_cwd(self, relative: str | None) -> tuple[Path, int | None]:
        """Create/open cwd beneath a pre-opened root without following symlinks."""

        raw = str(relative or "").strip()
        if not raw:
            if os.name == "posix":
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                return self.workspace, os.open(self.workspace, flags)
            return self.workspace, None
        path = Path(raw)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("process cwd escapes workspace")
        if os.name != "posix":
            candidate = self.workspace.joinpath(*path.parts)
            candidate.mkdir(parents=True, exist_ok=True)
            resolved = candidate.resolve()
            if not resolved.is_relative_to(self.workspace):
                raise ValueError("process cwd escapes workspace")
            return resolved, None

        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        current_fd = os.open(self.workspace, flags)
        try:
            for part in path.parts:
                if "\x00" in part or len(part) > 255:
                    raise ValueError("invalid process cwd component")
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                next_fd = os.open(part, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            return self.workspace.joinpath(*path.parts), current_fd
        except Exception:
            os.close(current_fd)
            raise

    @staticmethod
    def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None and os.name != "posix":
            return
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.kill()

    def _minimal_env(self) -> dict[str, str]:
        temp_root, temp_fd = self._open_safe_cwd(".tmp")
        if temp_fd is not None:
            os.close(temp_fd)
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

    @classmethod
    def _finite_positive_timeout(cls, value: Any, *, field: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field} must be a finite positive number")
        timeout = float(value)
        if not math.isfinite(timeout) or timeout <= 0.0:
            raise ValueError(f"{field} must be a finite positive number")
        if timeout > cls.MAX_TIMEOUT_SECONDS:
            raise ValueError(f"{field} exceeds the bounded process timeout")
        return timeout

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
            if not isinstance(value, str):
                return BodyResult(False, "process_run", error="process arguments must be strings")
            item = value
            if "\x00" in item or len(item) > self.MAX_ARG_CHARS:
                return BodyResult(False, "process_run", error="invalid process argument")
            argv.append(item)

        stdin_value = args.get("stdin", "")
        if not isinstance(stdin_value, str):
            return BodyResult(False, "process_run", error="stdin must be a string")
        stdin_text = stdin_value
        stdin_bytes = stdin_text.encode("utf-8")
        if len(stdin_bytes) > self.MAX_STDIN_BYTES:
            return BodyResult(False, "process_run", error="stdin exceeds bounded process limit")
        try:
            default_timeout = self._finite_positive_timeout(
                self.config.get("timeout_seconds", 30.0), field="timeout_seconds"
            )
            maximum_timeout = self._finite_positive_timeout(
                self.config.get("max_timeout_seconds", 120.0),
                field="max_timeout_seconds",
            )
            requested_timeout = self._finite_positive_timeout(
                args.get("timeout_seconds", default_timeout), field="timeout_seconds"
            )
        except ValueError as exc:
            return BodyResult(False, "process_run", error=str(exc))
        timeout = max(0.1, min(requested_timeout, maximum_timeout))
        cwd_value = args.get("cwd")
        if cwd_value is not None and not isinstance(cwd_value, str):
            return BodyResult(False, "process_run", error="cwd must be a relative string")
        cwd, cwd_fd = self._open_safe_cwd(cwd_value)

        sandbox = self._verified_sandbox_prefix()
        command = [*sandbox, executable, *argv] if sandbox else [executable, *argv]
        started = time.monotonic()
        timed_out = False
        output_limited = Event()
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        output_lock = Lock()
        total_output_bytes = 0
        truncated_streams: set[str] = set()
        popen_kwargs: dict[str, Any] = {
            "args": command,
            "cwd": (
                f"/proc/self/fd/{cwd_fd}"
                if cwd_fd is not None and Path("/proc/self/fd").is_dir()
                else str(cwd)
            ),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": self._minimal_env(),
            "shell": False,
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
            if cwd_fd is not None:
                popen_kwargs["pass_fds"] = (cwd_fd,)
        try:
            process = subprocess.Popen(**popen_kwargs)
        finally:
            if cwd_fd is not None:
                os.close(cwd_fd)

        def drain(stream: Any, target: bytearray, stream_name: str) -> None:
            nonlocal total_output_bytes
            try:
                while True:
                    chunk = stream.read(65_536)
                    if not chunk:
                        return
                    with output_lock:
                        remaining = self.MAX_OUTPUT_BYTES - total_output_bytes
                        accepted = min(len(chunk), max(0, remaining))
                        if accepted:
                            target.extend(chunk[:accepted])
                            total_output_bytes += accepted
                        exceeded = accepted < len(chunk)
                        if exceeded:
                            truncated_streams.add(stream_name)
                    if exceeded:
                        output_limited.set()
                        self._kill_process_group(process)
                        return
            except (OSError, ValueError):
                return

        def feed_stdin() -> None:
            if process.stdin is None:
                return
            try:
                process.stdin.write(stdin_bytes)
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass

        if process.stdout is None or process.stderr is None:
            self._kill_process_group(process)
            process.wait(timeout=5)
            raise RuntimeError("bounded process pipes were not created")
        readers = [
            Thread(target=drain, args=(process.stdout, stdout_buffer, "stdout"), daemon=True),
            Thread(target=drain, args=(process.stderr, stderr_buffer, "stderr"), daemon=True),
        ]
        writer = Thread(target=feed_stdin, daemon=True)
        for thread in [*readers, writer]:
            thread.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._kill_process_group(process)
            process.wait(timeout=5)
        finally:
            # A completed parent may have left descendants holding inherited pipes.
            self._kill_process_group(process)
            for thread in readers:
                thread.join(timeout=1)
            for stream in (process.stdout, process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            writer.join(timeout=1)

        stdout_raw = bytes(stdout_buffer)
        stderr_raw = bytes(stderr_buffer)

        duration_ms = (time.monotonic() - started) * 1000.0
        stdout_truncated = "stdout" in truncated_streams
        stderr_truncated = "stderr" in truncated_streams
        return BodyResult(
            ok=(not timed_out and not output_limited.is_set() and process.returncode == 0),
            capability="process_run",
            data={
                "executable": alias,
                "argv": argv,
                "cwd": str(cwd.relative_to(self.workspace)) if cwd != self.workspace else ".",
                "sandboxed": bool(sandbox),
                "returncode": process.returncode,
                "timed_out": timed_out,
                "output_limited": output_limited.is_set(),
                "duration_ms": duration_ms,
                "stdout": stdout_raw.decode("utf-8", errors="replace"),
                "stderr": stderr_raw.decode("utf-8", errors="replace"),
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            },
            error=(
                "process timed out"
                if timed_out
                else (
                    "process output exceeded bounded limit"
                    if output_limited.is_set()
                    else (None if process.returncode == 0 else f"process exited with {process.returncode}")
                )
            ),
        )
