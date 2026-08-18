from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


SAFE_LOCAL_SCHEMES = {"about", "data", "blob"}


def assert_http_url(url: str, *, allow_private: bool = False) -> None:
    parsed = urlparse(str(url))
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are permitted")
    if not parsed.hostname:
        raise ValueError("URL hostname is required")
    if parsed.username or parsed.password:
        raise ValueError("Credentials embedded in URLs are not permitted")
    if allow_private:
        return
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    for info in addresses:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(f"Non-public destination rejected: {ip}")


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
