import asyncio
from pathlib import Path
from ocpp import store


def test_session_lockfile_created_and_removed(tmp_path, monkeypatch):
    lock_file = tmp_path / "charging.lck"
    monkeypatch.setattr(store, "LOCK_DIR", tmp_path)
    monkeypatch.setattr(store, "SESSION_LOCK", lock_file)
    store._lock_task = None

    async def run():
        store.start_session_lock()
        await asyncio.sleep(0.1)
        assert lock_file.exists()
        store.stop_session_lock()
        await asyncio.sleep(0)
        assert not lock_file.exists()

    asyncio.run(run())
