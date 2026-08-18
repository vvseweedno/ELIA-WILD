from __future__ import annotations

import asyncio
from concurrent.futures import Future
from threading import Thread
from typing import Awaitable, TypeVar


T = TypeVar("T")


def run_sync(awaitable: Awaitable[T]) -> T:
    """Run an async MCP operation from ELIA's synchronous runtime safely.

    If no event loop is active in the current thread, use asyncio.run directly.
    If a host already owns the current thread's loop, execute the coroutine in a
    dedicated thread with its own loop rather than attempting nested asyncio.run().
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    future: Future[T] = Future()

    def worker() -> None:
        try:
            future.set_result(asyncio.run(awaitable))
        except BaseException as exc:
            future.set_exception(exc)

    thread = Thread(target=worker, name="elia-async-bridge", daemon=True)
    thread.start()
    try:
        return future.result()
    finally:
        thread.join(timeout=1.0)
