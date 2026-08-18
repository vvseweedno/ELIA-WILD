from __future__ import annotations

import asyncio

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
