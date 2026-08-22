from __future__ import annotations

import asyncio
import time

import pytest

from elia.body.asyncio_bridge import run_sync


async def _value() -> int:
    await asyncio.sleep(0.01)
    return 42


def test_asyncio_bridge_without_running_loop() -> None:
    assert run_sync(_value()) == 42


def test_asyncio_bridge_inside_running_loop_uses_isolated_thread() -> None:
    async def host() -> int:
        return run_sync(_value())

    assert asyncio.run(host()) == 42


def test_asyncio_bridge_enforces_aggregate_operation_deadline() -> None:
    async def slow() -> int:
        await asyncio.sleep(1)
        return 1

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        run_sync(slow(), timeout_seconds=0.02)
    assert time.monotonic() - started < 0.5
