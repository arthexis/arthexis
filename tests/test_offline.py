import sys
from pathlib import Path

import asyncio
import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config.offline import requires_network, OfflineError


@requires_network
def sync_func():
    return "ok"


@requires_network
async def async_func():
    return "ok"


def test_sync_function_offline(monkeypatch):
    monkeypatch.setenv("ARTHEXIS_OFFLINE", "1")
    with pytest.raises(OfflineError):
        sync_func()


def test_async_function_offline(monkeypatch):
    monkeypatch.setenv("ARTHEXIS_OFFLINE", "1")
    with pytest.raises(OfflineError):
        asyncio.run(async_func())


def test_release_build_offline(monkeypatch):
    from core.release import build

    monkeypatch.setenv("ARTHEXIS_OFFLINE", "1")
    with pytest.raises(OfflineError):
        build()
