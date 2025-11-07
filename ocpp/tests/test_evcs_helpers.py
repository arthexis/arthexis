from __future__ import annotations

import asyncio
import math
from unittest.mock import Mock

import pytest

from ocpp import evcs


@pytest.mark.parametrize(
    "value, expected",
    [
        (True, math.inf),
        ("Forever", math.inf),
        ("loop", math.inf),
        ("3", 3),
        (3, 3),
        (-2, 1),
        ("invalid", 1),
        (object(), 1),
    ],
)
def test_parse_repeat_handles_various_inputs(value, expected):
    result = evcs.parse_repeat(value)

    if math.isinf(expected):
        assert math.isinf(result)
    else:
        assert result == expected


def test_unique_cp_path_generates_suffix_for_multiple_threads():
    base = "CP123"
    unique = evcs._unique_cp_path(base, idx=0, total_threads=3)

    assert unique.startswith(f"{base}-")
    assert len(unique) == len(base) + 5  # ``-`` + four hex digits


def test_unique_cp_path_returns_base_for_single_thread():
    base = "CP123"
    assert evcs._unique_cp_path(base, idx=0, total_threads=1) == base


def test_thread_runner_executes_coroutine_with_asyncio_run(monkeypatch):
    called = {}

    async def sample(arg, *, kw):  # pragma: no cover - executed via asyncio.run
        called["value"] = (arg, kw)

    fake_run = Mock(side_effect=lambda coro: asyncio.get_event_loop().run_until_complete(coro))

    loop = asyncio.new_event_loop()
    monkeypatch.setattr(asyncio, "run", fake_run)
    try:
        asyncio.set_event_loop(loop)
        evcs._thread_runner(sample, 5, kw="sentinel")
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    assert called["value"] == (5, "sentinel")
    fake_run.assert_called_once()
