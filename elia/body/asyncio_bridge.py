from __future__ import annotations

import asyncio
from concurrent.futures import Future
import math
from threading import Thread
from typing import Any, Coroutine, TypeVar


T = TypeVar("T")


def run_sync(awaitable: Coroutine[Any, Any, T], *, timeout_seconds: float = 600.0) -> T:
    """Run an async MCP operation from ELIA's synchronous runtime safely.

    If no event loop is active in the current thread, use asyncio.run directly.
    If a host already owns the current thread's loop, execute the coroutine in a
    dedicated thread with its own loop rather than attempting nested asyncio.run().
    """

    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise ValueError("async bridge timeout must be a finite positive number")
    timeout = float(timeout_seconds)
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 600.0:
        raise ValueError("async bridge timeout must be finite, positive and at most 600 seconds")

    async def bounded() -> T:
        return await asyncio.wait_for(awaitable, timeout=timeout)

    bounded_awaitable = bounded()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(bounded_awaitable)

    future: Future[T] = Future()

    def worker() -> None:
        try:
            future.set_result(asyncio.run(bounded_awaitable))
        except BaseException as exc:
            future.set_exception(exc)

    thread = Thread(target=worker, name="elia-async-bridge", daemon=True)
    thread.start()
    try:
        return future.result()
    finally:
        thread.join(timeout=1.0)
