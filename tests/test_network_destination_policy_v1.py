from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
from threading import Event

import pytest

from elia.body.net import (
    _bounded_getaddrinfo,
    _reject_nonpublic_ip,
    network_isolation_attested,
    resolve_http_target,
)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "100.64.0.1",
        "192.0.2.1",
        "169.254.169.254",
        "::1",
        "fc00::1",
        "fe80::1",
        "2001:db8::1",
        "0.0.0.0",
        "224.0.0.1",
    ],
)
def test_default_network_policy_rejects_non_global_addresses(address: str) -> None:
    with pytest.raises(ValueError):
        _reject_nonpublic_ip(address)


def test_explicit_private_mode_allows_private_and_loopback_only() -> None:
    assert _reject_nonpublic_ip("10.0.0.1", allow_private=True) == "10.0.0.1"
    assert _reject_nonpublic_ip("127.0.0.1", allow_private=True) == "127.0.0.1"
    assert _reject_nonpublic_ip("fc00::1", allow_private=True) == "fc00::1"

    for address in ("169.254.169.254", "fe80::1", "224.0.0.1", "0.0.0.0"):
        with pytest.raises(ValueError):
            _reject_nonpublic_ip(address, allow_private=True)


def test_public_addresses_remain_permitted() -> None:
    assert _reject_nonpublic_ip("8.8.8.8") == "8.8.8.8"
    assert _reject_nonpublic_ip("2606:4700:4700::1111") == "2606:4700:4700::1111"


def test_legacy_boolean_is_not_network_isolation_evidence() -> None:
    assert network_isolation_attested({"network_isolation_confirmed": True}) is False
    assert network_isolation_attested({}, verifier=lambda policy: True) is True


def test_network_isolation_witness_is_namespace_bound_and_not_runtime_owned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    witness = tmp_path / "network-isolation.json"
    raw = json.dumps(
        {
            "schema_version": 1,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "network_namespace_inode": os.stat("/proc/self/ns/net").st_ino,
            "denied_scopes": [
                "loopback",
                "private",
                "link_local",
                "metadata",
                "multicast",
                "reserved",
            ],
            "mechanism": "network_namespace",
        }
    ).encode("utf-8")
    witness.write_bytes(raw)
    witness.chmod(0o400)
    # The test runner is root in CI, while production must separate the witness owner
    # from the organism runtime UID. Model that privilege separation explicitly.
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    config = {
        "network_isolation": {
            "attestation_path": str(witness),
            "attestation_sha256": sha256(raw).hexdigest(),
            "attestation_owner_uid": os.stat(witness).st_uid,
        }
    }
    assert network_isolation_attested(config) is True

    witness.chmod(0o600)
    witness.write_text("{}", encoding="utf-8")
    witness.chmod(0o400)
    assert network_isolation_attested(config) is False

    witness.unlink()
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(raw)
    witness.symlink_to(replacement)
    assert network_isolation_attested(config) is False


def test_runtime_owned_or_owner_writable_witness_cannot_authorize_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    witness = tmp_path / "network-isolation.json"
    raw = json.dumps(
        {
            "schema_version": 1,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "network_namespace_inode": os.stat("/proc/self/ns/net").st_ino,
            "denied_scopes": sorted(
                {"loopback", "private", "link_local", "metadata", "multicast", "reserved"}
            ),
            "mechanism": "network_namespace",
        }
    ).encode()
    witness.write_bytes(raw)
    witness.chmod(0o400)
    owner = os.stat(witness).st_uid
    config = {
        "network_isolation": {
            "attestation_path": str(witness),
            "attestation_sha256": sha256(raw).hexdigest(),
            "attestation_owner_uid": owner,
        }
    }

    monkeypatch.setattr(os, "geteuid", lambda: owner)
    assert network_isolation_attested(config) is False

    monkeypatch.setattr(os, "geteuid", lambda: owner + 1)
    witness.chmod(0o600)
    assert network_isolation_attested(config) is False


def test_dns_resolution_has_a_real_deadline(monkeypatch) -> None:
    entered = Event()
    release = Event()

    def blocking_getaddrinfo(*args, **kwargs):
        entered.set()
        release.wait(timeout=2)
        return socket.getaddrinfo("127.0.0.1", 80, type=socket.SOCK_STREAM)

    original = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", blocking_getaddrinfo)
    try:
        with pytest.raises(TimeoutError, match="DNS resolution exceeded"):
            resolve_http_target(
                "http://example.test",
                allow_private=True,
                resolver_timeout=0.05,
            )
        assert entered.wait(timeout=1)
    finally:
        monkeypatch.setattr(socket, "getaddrinfo", original)
        release.set()


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), 0.0, -1.0, True, "1"])
def test_dns_resolution_rejects_invalid_timeout_without_starting_worker(
    monkeypatch: pytest.MonkeyPatch,
    invalid: object,
) -> None:
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    with pytest.raises(ValueError, match="finite positive"):
        _bounded_getaddrinfo("example.test", 443, timeout=invalid)
    assert called is False
