"""The server's loop owns the scheduler and the live streams; routes run in
worker threads. What a route sets off there goes through call_on."""

import asyncio
from collections.abc import Callable


def call_on(loop: asyncio.AbstractEventLoop | None, function: Callable, *args) -> None:
    """function(*args) on loop: now when this thread runs it (or there is
    none yet), else as soon as the loop gets to it."""
    try:
        here = asyncio.get_running_loop() is loop
    except RuntimeError:
        here = False
    if loop is None or here:
        function(*args)
    else:
        loop.call_soon_threadsafe(function, *args)
