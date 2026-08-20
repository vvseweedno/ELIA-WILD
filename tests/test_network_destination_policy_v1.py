from __future__ import annotations

import pytest

from elia.body.net import _reject_nonpublic_ip


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
