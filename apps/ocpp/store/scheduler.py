"""Scheduler loop/thread helpers for the OCPP store."""

from __future__ import annotations

import asyncio
import threading

_scheduler_loop: asyncio.AbstractEventLoop | None = None
_scheduler_thread: threading.Thread | None = None
_scheduler_lock = threading.Lock()


def _run_scheduler_loop(
    loop: asyncio.AbstractEventLoop, ready: threading.Event
) -> None:
    asyncio.set_event_loop(loop)
    ready.set()
    loop.run_forever()


def _ensure_scheduler_loop() -> asyncio.AbstractEventLoop:
    global _scheduler_loop, _scheduler_thread

    loop = _scheduler_loop
    if loop and loop.is_running():
        return loop
    with _scheduler_lock:
        loop = _scheduler_loop
        if loop and loop.is_running():
            return loop
        loop = asyncio.new_event_loop()
        ready = threading.Event()
        thread = threading.Thread(
            target=_run_scheduler_loop,
            args=(loop, ready),
            name="ocpp-store-scheduler",
            daemon=True,
        )
        thread.start()
        ready.wait()
        _scheduler_loop = loop
        _scheduler_thread = thread
        return loop


def _cancel_timer_handle(handle: asyncio.TimerHandle) -> None:
    loop = _scheduler_loop
    if loop and loop.is_running():
        loop.call_soon_threadsafe(handle.cancel)
    else:  # pragma: no cover - loop stopped during shutdown
        handle.cancel()


__all__: list[str] = []
