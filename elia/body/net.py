from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlparse
from typing import Mapping


SAFE_LOCAL_SCHEMES = {"about", "data", "blob"}


@dataclass(frozen=True, slots=True)
class PinnedHTTPResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    truncated: bool
    encoding: str | None
    peer_ip: str


def _reject_nonpublic_ip(value: str, *, allow_private: bool = False) -> str:
    ip = ipaddress.ip_address(value)
    if not allow_private and (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise ValueError(f"Non-public destination rejected: {ip}")
    return str(ip)


def resolve_http_target(url: str, *, allow_private: bool = False) -> tuple[str, int, list[str]]:
    parsed = urlparse(str(url))
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are permitted")
    if not parsed.hostname:
        raise ValueError("URL hostname is required")
    if parsed.username or parsed.password:
        raise ValueError("Credentials embedded in URLs are not permitted")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses: list[str] = []
    for info in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM):
        address = _reject_nonpublic_ip(info[4][0], allow_private=allow_private)
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise OSError(f"hostname resolved to no usable addresses: {parsed.hostname}")
    return parsed.hostname, port, addresses


def assert_http_url(url: str, *, allow_private: bool = False) -> None:
    resolve_http_target(url, allow_private=allow_private)


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

    parsed = urlparse(str(url))
    hostname, port, addresses = resolve_http_target(url, allow_private=allow_private)
    timeout = max(0.5, min(float(timeout), 120.0))
    max_bytes = max(1, min(int(max_bytes), 8_000_000))
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    default_port = 443 if parsed.scheme == "https" else 80
    host_name_for_header = f"[{hostname}]" if ":" in hostname else hostname
    host_header = host_name_for_header if port == default_port else f"{host_name_for_header}:{port}"
    request_headers: dict[str, str] = {
        "Host": host_header,
        "Connection": "close",
        "Accept-Encoding": "identity",
    }
    for raw_name, raw_value in (headers or {}).items():
        name, value = _header_pair(str(raw_name), str(raw_value))
        if name.lower() in {"host", "connection", "content-length", "transfer-encoding"}:
            raise ValueError(f"caller may not override transport-owned HTTP header: {name}")
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
            peer_ip = _reject_nonpublic_ip(raw_socket.getpeername()[0], allow_private=allow_private)
            if parsed.scheme == "https":
                context = ssl.create_default_context()
                stream = context.wrap_socket(raw_socket, server_hostname=hostname)
                raw_socket = None
            else:
                stream = raw_socket
                raw_socket = None

            request = [f"{method} {path} HTTP/1.1"]
            request.extend(f"{key}: {value}" for key, value in request_headers.items())
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
