from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
from queue import Empty, Queue
import socket
import ssl
import stat
from threading import BoundedSemaphore, Thread
from urllib.parse import urlparse
from typing import Any, Callable, Mapping


SAFE_LOCAL_SCHEMES = {"about", "data", "blob"}
MAX_RESOLVED_ADDRESSES = 32
DEFAULT_RESOLVER_TIMEOUT_SECONDS = 5.0
_RESOLVER_SLOTS = BoundedSemaphore(8)
_REQUIRED_DENIED_NETWORK_SCOPES = frozenset(
    {"loopback", "private", "link_local", "metadata", "multicast", "reserved"}
)


@dataclass(frozen=True, slots=True)
class PinnedHTTPResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    truncated: bool
    encoding: str | None
    peer_ip: str


def network_isolation_attested(
    config: Mapping[str, Any],
    *,
    verifier: Callable[[dict[str, Any]], bool] | None = None,
) -> bool:
    """Validate a deployment-produced, namespace-bound network policy witness.

    The historical `network_isolation_confirmed: true` flag is deliberately ignored: a
    boolean is not evidence. Tests/in-process adapters may inject a verifier callable;
    production configuration must reference an immutable JSON witness owned outside
    the runtime UID, bound to the current Linux network namespace and an unexpired deny
    policy. A root-running organism cannot authenticate a root-owned file boundary and
    must use an injected kernel verifier instead.
    """

    policy = config.get("network_isolation") or {}
    if not isinstance(policy, dict):
        return False
    if verifier is not None:
        try:
            return bool(verifier(dict(policy)))
        except Exception:
            return False
    witness_path = str(policy.get("attestation_path", "")).strip()
    if not witness_path or os.name != "posix":
        return False
    path = Path(witness_path)
    try:
        if not path.is_absolute() or path.is_symlink():
            return False
        expected_digest = str(policy.get("attestation_sha256", "")).strip().lower()
        expected_owner = policy.get("attestation_owner_uid")
        runtime_uid = os.geteuid()
        if (
            len(expected_digest) != 64
            or any(character not in "0123456789abcdef" for character in expected_digest)
            or isinstance(expected_owner, bool)
            or not isinstance(expected_owner, int)
            or expected_owner < 0
            or expected_owner == runtime_uid
        ):
            return False
        parent_metadata = path.parent.stat()
        if not stat.S_ISDIR(parent_metadata.st_mode) or parent_metadata.st_mode & 0o022:
            return False
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_mode & 0o222
                or metadata.st_uid != expected_owner
                or parent_metadata.st_uid not in {0, expected_owner}
            ):
                return False
            chunks = bytearray()
            while len(chunks) <= 65_536:
                chunk = os.read(descriptor, min(65_537 - len(chunks), 16_384))
                if not chunk:
                    break
                chunks.extend(chunk)
            if len(chunks) > 65_536:
                return False
        finally:
            os.close(descriptor)
        if not hmac.compare_digest(sha256(bytes(chunks)).hexdigest(), expected_digest):
            return False
        witness = json.loads(bytes(chunks).decode("utf-8"))
        if not isinstance(witness, dict) or int(witness.get("schema_version", 0)) != 1:
            return False
        expires_at = datetime.fromisoformat(
            str(witness.get("expires_at", "")).replace("Z", "+00:00")
        )
        if expires_at.tzinfo is None:
            return False
        if expires_at.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            return False
        current_namespace = int(os.stat("/proc/self/ns/net").st_ino)
        if int(witness.get("network_namespace_inode", -1)) != current_namespace:
            return False
        denied = {str(item).strip().lower() for item in witness.get("denied_scopes", [])}
        if not _REQUIRED_DENIED_NETWORK_SCOPES.issubset(denied):
            return False
        mechanism = str(witness.get("mechanism", "")).strip().lower()
        if mechanism not in {"network_namespace", "container_firewall", "host_firewall"}:
            return False
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    return True


def _bounded_getaddrinfo(
    hostname: str,
    port: int,
    *,
    timeout: Any,
) -> list[tuple[Any, ...]]:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("DNS resolver timeout must be a finite positive number")
    timeout = float(timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("DNS resolver timeout must be a finite positive number")
    timeout = max(0.05, min(timeout, 30.0))
    if not _RESOLVER_SLOTS.acquire(blocking=False):
        raise TimeoutError("DNS resolver concurrency bound is exhausted")
    result_queue: Queue[tuple[bool, Any]] = Queue(maxsize=1)

    def worker() -> None:
        try:
            result_queue.put(
                (True, socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)),
                block=False,
            )
        except BaseException as exc:
            try:
                result_queue.put((False, exc), block=False)
            except Exception:
                pass
        finally:
            _RESOLVER_SLOTS.release()

    thread = Thread(target=worker, name="elia-bounded-dns", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"DNS resolution exceeded {timeout:.2f}s")
    try:
        ok, value = result_queue.get_nowait()
    except Empty as exc:
        raise OSError("DNS resolver returned no result") from exc
    if not ok:
        raise value
    return list(value)[:MAX_RESOLVED_ADDRESSES]


def _reject_nonpublic_ip(value: str, *, allow_private: bool = False) -> str:
    """Return a normalized usable destination or reject it.

    Public networking is default-deny: only globally routable addresses are accepted.
    `allow_private` is a narrow explicit escape for private/loopback deployments; it
    never permits link-local metadata ranges, multicast, reserved or unspecified space.
    """

    ip = ipaddress.ip_address(value)
    always_forbidden = (
        ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )
    if always_forbidden:
        raise ValueError(f"Unsafe destination rejected: {ip}")
    if not allow_private and not ip.is_global:
        # Keep the established API/error contract while enforcing the stronger
        # globally-routable policy (including CGNAT/documentation/ULA space).
        raise ValueError(f"Non-public destination rejected: {ip}")
    return str(ip)


def resolve_http_target(
    url: str,
    *,
    allow_private: bool = False,
    resolver_timeout: float = DEFAULT_RESOLVER_TIMEOUT_SECONDS,
) -> tuple[str, int, list[str]]:
    parsed = urlparse(str(url))
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are permitted")
    if not parsed.hostname:
        raise ValueError("URL hostname is required")
    if parsed.username or parsed.password:
        raise ValueError("Credentials embedded in URLs are not permitted")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses: list[str] = []
    for info in _bounded_getaddrinfo(
        parsed.hostname,
        port,
        timeout=resolver_timeout,
    ):
        address = _reject_nonpublic_ip(info[4][0], allow_private=allow_private)
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise OSError(f"hostname resolved to no usable addresses: {parsed.hostname}")
    return parsed.hostname, port, addresses


def assert_http_url(
    url: str,
    *,
    allow_private: bool = False,
    resolver_timeout: float = DEFAULT_RESOLVER_TIMEOUT_SECONDS,
) -> None:
    resolve_http_target(
        url,
        allow_private=allow_private,
        resolver_timeout=resolver_timeout,
    )


def is_safe_browser_subresource(url: str, *, allow_private: bool = False) -> bool:
    parsed = urlparse(str(url))
    if parsed.scheme in SAFE_LOCAL_SCHEMES:
        return True
    if parsed.scheme not in {"http", "https"}:
        return False
    try:
        assert_http_url(url, allow_private=allow_private)
    except Exception:
        return False
    return True


def _header_pair(name: str, value: str) -> tuple[str, str]:
    name = str(name)
    value = str(value)
    if not name or any(ch in name for ch in "\r\n:"):
        raise ValueError("invalid HTTP header name")
    if any(ch in value for ch in "\r\n"):
        raise ValueError(f"invalid HTTP header value for {name!r}")
    return name, value


def pinned_http_request(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    timeout: float = 20.0,
    max_bytes: int = 1_000_000,
    max_request_bytes: int = 1_000_000,
    headers: Mapping[str, str] | None = None,
    allow_private: bool = False,
) -> PinnedHTTPResponse:
    """Perform one HTTP request pinned to a prevalidated resolved IP.

    The hostname is retained for HTTP Host and TLS SNI/certificate verification, but
    TCP connects only to an address that was already validated. The connected peer is
    checked again after connect. Redirects are never followed automatically.
    """

    method = str(method).strip().upper()
    if method not in {"GET", "POST"}:
        raise ValueError("pinned transport currently permits only GET and POST")
    body = bytes(body or b"")
    max_request_bytes = max(0, min(int(max_request_bytes), 8_000_000))
    if len(body) > max_request_bytes:
        raise ValueError("HTTP request body exceeds configured limit")

    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("HTTP timeout must be a finite positive number")
    timeout = float(timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("HTTP timeout must be a finite positive number")
    timeout = max(0.5, min(timeout, 120.0))
    parsed = urlparse(str(url))
    hostname, port, addresses = resolve_http_target(
        url,
        allow_private=allow_private,
        resolver_timeout=min(timeout, DEFAULT_RESOLVER_TIMEOUT_SECONDS),
    )
    max_bytes = max(1, min(int(max_bytes), 8_000_000))
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    default_port = 443 if parsed.scheme == "https" else 80
    host_name_for_header = f"[{hostname}]" if ":" in hostname else hostname
    host_header = (
        host_name_for_header
        if port == default_port
        else f"{host_name_for_header}:{port}"
    )
    request_headers: dict[str, str] = {
        "Host": host_header,
        "Connection": "close",
        "Accept-Encoding": "identity",
    }
    for raw_name, raw_value in (headers or {}).items():
        name, value = _header_pair(str(raw_name), str(raw_value))
        if name.lower() in {
            "host",
            "connection",
            "content-length",
            "transfer-encoding",
        }:
            raise ValueError(
                f"caller may not override transport-owned HTTP header: {name}"
            )
        request_headers[name] = value
    if body:
        request_headers["Content-Length"] = str(len(body))

    last_error: Exception | None = None
    for address in addresses:
        raw_socket: socket.socket | None = None
        stream: socket.socket | None = None
        try:
            raw_socket = socket.create_connection((address, port), timeout=timeout)
            raw_socket.settimeout(timeout)
            peer_ip = _reject_nonpublic_ip(
                raw_socket.getpeername()[0], allow_private=allow_private
            )
            if parsed.scheme == "https":
                context = ssl.create_default_context()
                stream = context.wrap_socket(raw_socket, server_hostname=hostname)
                raw_socket = None
            else:
                stream = raw_socket
                raw_socket = None

            request = [f"{method} {path} HTTP/1.1"]
            request.extend(
                f"{key}: {value}" for key, value in request_headers.items()
            )
            wire = ("\r\n".join(request) + "\r\n\r\n").encode("iso-8859-1") + body
            stream.sendall(wire)

            response = http.client.HTTPResponse(stream)
            response.begin()
            content = response.read(max_bytes + 1)
            truncated = len(content) > max_bytes
            content = content[:max_bytes]
            response_headers = {str(k): str(v) for k, v in response.getheaders()}
            encoding = response.headers.get_content_charset()
            return PinnedHTTPResponse(
                url=str(url),
                status_code=int(response.status),
                headers=response_headers,
                content=content,
                truncated=truncated,
                encoding=encoding,
                peer_ip=peer_ip,
            )
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
        finally:
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
            if raw_socket is not None:
                try:
                    raw_socket.close()
                except OSError:
                    pass
    if last_error is not None:
        raise last_error
    raise OSError("no validated destination address could be connected")


def pinned_http_get(
    url: str,
    *,
    timeout: float = 20.0,
    max_bytes: int = 1_000_000,
    headers: Mapping[str, str] | None = None,
    allow_private: bool = False,
) -> PinnedHTTPResponse:
    return pinned_http_request(
        "GET",
        url,
        timeout=timeout,
        max_bytes=max_bytes,
        headers=headers,
        allow_private=allow_private,
    )
