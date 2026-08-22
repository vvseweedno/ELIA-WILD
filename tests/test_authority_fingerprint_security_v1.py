from __future__ import annotations

import pytest

from elia.owner_control import arguments_fingerprint
from elia.resource_ingress import _canonical as ingress_canonical
from elia.work_ports import _fingerprint as work_port_fingerprint


@pytest.mark.parametrize(
    "canonicalizer",
    [
        lambda value: arguments_fingerprint("submit_work", value),
        work_port_fingerprint,
        ingress_canonical,
    ],
)
def test_authority_and_idempotency_canonicalizers_are_order_stable(canonicalizer) -> None:
    assert canonicalizer({"a": 1, "nested": {"x": 2, "y": 3}}) == canonicalizer(
        {"nested": {"y": 3, "x": 2}, "a": 1}
    )


@pytest.mark.parametrize(
    "canonicalizer",
    [
        lambda value: arguments_fingerprint("submit_work", value),
        work_port_fingerprint,
        ingress_canonical,
    ],
)
def test_authority_and_idempotency_canonicalizers_reject_ambiguous_values(
    canonicalizer,
) -> None:
    class Stringifiable:
        def __str__(self) -> str:
            return "ambiguous"

    for invalid in ({"amount": float("nan")}, {"value": Stringifiable()}):
        with pytest.raises(ValueError):
            canonicalizer(invalid)
