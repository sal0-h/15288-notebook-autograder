"""Server-Sent Events helpers for long-running pipeline work."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable

from sse_starlette.sse import EventSourceResponse


async def threaded_sse_response(
    lock: threading.Lock,
    worker: Callable[[Callable[[dict], None]], None],
    on_event: Callable[[dict], None] | None = None,
) -> EventSourceResponse:
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def run_worker() -> None:
        try:
            worker(emit)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)
            lock.release()

    try:
        threading.Thread(target=run_worker, daemon=True).start()
    except Exception:
        lock.release()
        raise

    async def event_generator():
        try:
            while True:
                evt = await queue.get()
                if evt is None:
                    yield {"event": "done", "data": "{}"}
                    return
                if on_event is not None:
                    on_event(evt)
                yield {"event": "progress", "data": json.dumps(evt)}
        except GeneratorExit:
            pass

    return EventSourceResponse(event_generator())
