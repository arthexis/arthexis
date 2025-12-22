import asyncio
import fnmatch
from collections import defaultdict

import pytest
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.core.cache import cache
from django.test.utils import override_settings

from apps.ocpp import store
from config.asgi import application

pytestmark = pytest.mark.django_db(transaction=True)


class FakeRedis:
    """Minimal Redis stub that supports the store's usage patterns."""

    def __init__(self):
        self._data: dict[str, str] = {}
        self._sets: defaultdict[str, set[str]] = defaultdict(set)

    def set(self, key: str, value: str, ex: int | None = None):  # pragma: no cover - trivial
        self._data[key] = value

    def get(self, key: str):  # pragma: no cover - trivial
        return self._data.get(key)

    def delete(self, *keys: str) -> int:  # pragma: no cover - trivial
        removed = 0
        for key in keys:
            if key in self._data:
                removed += 1
                self._data.pop(key, None)
            if key in self._sets:
                removed += 1
                self._sets.pop(key, None)
        return removed

    def scan_iter(self, pattern: str):  # pragma: no cover - simple generator
        for key in list(self._data.keys()):
            if fnmatch.fnmatch(key, pattern):
                yield key

    def sadd(self, key: str, value: str) -> int:  # pragma: no cover - trivial
        members = self._sets[key]
        before = len(members)
        members.add(value)
        return int(len(members) > before)

    def srem(self, key: str, value: str) -> int:  # pragma: no cover - trivial
        members = self._sets.get(key)
        if members is None:
            return 0
        removed = int(value in members)
        members.discard(value)
        return removed

    def scard(self, key: str) -> int:  # pragma: no cover - trivial
        return len(self._sets.get(key, set()))

    def expire(self, key: str, ttl: int):  # pragma: no cover - trivial
        return True

    def pipeline(self):  # pragma: no cover - trivial
        return _FakeRedisPipeline(self)


class _FakeRedisPipeline:
    def __init__(self, client: FakeRedis):
        self.client = client
        self.commands: list[tuple[str, tuple[object, ...]]] = []

    def sadd(self, key: str, value: str):  # pragma: no cover - trivial
        self.commands.append(("sadd", (key, value)))
        return self

    def expire(self, key: str, ttl: int):  # pragma: no cover - trivial
        self.commands.append(("expire", (key, ttl)))
        return self

    def scard(self, key: str):  # pragma: no cover - trivial
        self.commands.append(("scard", (key,)))
        return self

    def execute(self):  # pragma: no cover - trivial
        results = []
        for method, args in self.commands:
            results.append(getattr(self.client, method)(*args))
        return results


@pytest.fixture(autouse=True)
def clear_store_state():
    cache.clear()
    store.connections.clear()
    store.ip_connections.clear()
    store.logs["charger"].clear()
    store.log_names["charger"].clear()
    store.pending_calls.clear()
    store._pending_call_events.clear()
    store._pending_call_results.clear()
    store._pending_call_handles.clear()
    store.triggered_followups.clear()
    store.monitoring_report_requests.clear()
    yield
    cache.clear()
    store.connections.clear()
    store.ip_connections.clear()
    store.logs["charger"].clear()
    store.log_names["charger"].clear()
    store.pending_calls.clear()
    store._pending_call_events.clear()
    store._pending_call_results.clear()
    store._pending_call_handles.clear()
    store.triggered_followups.clear()
    store.monitoring_report_requests.clear()
    store._STATE_REDIS = None
    store._STATE_REDIS_URL = getattr(settings, "OCPP_STATE_REDIS_URL", "")


@pytest.fixture()
def fake_state_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(store, "_STATE_REDIS", fake)
    monkeypatch.setattr(store, "_STATE_REDIS_URL", "redis://test")
    monkeypatch.setattr(store, "_state_redis", lambda: fake)
    yield fake
    store._STATE_REDIS = None
    store._STATE_REDIS_URL = getattr(settings, "OCPP_STATE_REDIS_URL", "")


@pytest.fixture()
def temp_store_dirs(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    session_dir = log_dir / "sessions"
    lock_dir = tmp_path / "locks"
    session_dir.mkdir(parents=True, exist_ok=True)
    lock_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(store, "LOG_DIR", log_dir)
    monkeypatch.setattr(store, "SESSION_DIR", session_dir)
    monkeypatch.setattr(store, "LOCK_DIR", lock_dir)
    monkeypatch.setattr(store, "SESSION_LOCK", lock_dir / "charging.lck")
    yield


async def _wait_for_pending_result(message_id: str, timeout: float = 2.0):
    return await asyncio.wait_for(
        asyncio.to_thread(store.wait_for_pending_call, message_id, timeout=timeout),
        timeout=timeout + 0.25,
    )


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_reconnect_resumes_pending_call(fake_state_redis, temp_store_dirs):
    async def run_scenario():
        serial = "CP-RESUME"
        message_id = "resume-1"
        metadata = {
            "charger_id": serial,
            "action": "RemoteStartTransaction",
            "log_key": store.identity_key(serial, None),
        }
        store.register_pending_call(message_id, metadata)

        store.pending_calls.clear()
        store._pending_call_events.clear()
        store._pending_call_results.clear()
        store._pending_call_handles.clear()
        store.monitoring_report_requests.clear()
        store.monitoring_report_requests.clear()

        restored = store.restore_pending_calls(serial)
        assert message_id in restored
        assert message_id in store.pending_calls

        communicator = WebsocketCommunicator(application, f"/{serial}")
        connected, _ = await communicator.connect()
        assert connected is True

        await communicator.send_json_to([3, message_id, {"status": "Accepted"}])
        result = await _wait_for_pending_result(message_id)
        assert result is not None
        assert result["payload"]["status"] == "Accepted"

        await communicator.disconnect()

    async_to_sync(run_scenario)()


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_reconnect_resumes_pending_call_case_insensitive(
    fake_state_redis, temp_store_dirs
):
    async def run_scenario():
        serial = "CP-RESUME"
        message_id = "resume-lower-1"
        metadata = {
            "charger_id": serial.lower(),
            "action": "RemoteStartTransaction",
            "log_key": store.identity_key(serial, None),
        }
        store.register_pending_call(message_id, metadata)

        store.pending_calls.clear()
        store._pending_call_events.clear()
        store._pending_call_results.clear()
        store._pending_call_handles.clear()
        store.monitoring_report_requests.clear()
        store.monitoring_report_requests.clear()

        restored = store.restore_pending_calls(serial)
        assert message_id in restored
        assert message_id in store.pending_calls

        communicator = WebsocketCommunicator(application, f"/{serial}")
        connected, _ = await communicator.connect()
        assert connected is True

        await communicator.send_json_to([3, message_id, {"status": "Accepted"}])
        result = await _wait_for_pending_result(message_id)
        assert result is not None
        assert result["payload"]["status"] == "Accepted"

        await communicator.disconnect()

    async_to_sync(run_scenario)()


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_replayed_result_keeps_pending_queue_intact(fake_state_redis, temp_store_dirs):
    async def run_scenario():
        serial = "CP-REPLAY"
        completed_id = "done-1"
        pending_id = "pending-2"
        metadata = {
            "charger_id": serial,
            "action": "RemoteStartTransaction",
            "log_key": store.identity_key(serial, None),
        }
        store.register_pending_call(completed_id, metadata)
        store.register_pending_call(pending_id, metadata)
        store.pop_pending_call(completed_id)

        store.pending_calls.clear()
        store._pending_call_events.clear()
        store._pending_call_results.clear()
        store._pending_call_handles.clear()
        store.monitoring_report_requests.clear()

        restored = store.restore_pending_calls(serial)
        assert pending_id in restored
        assert pending_id in store.pending_calls
        assert completed_id not in store.pending_calls

        communicator = WebsocketCommunicator(application, f"/{serial}")
        connected, _ = await communicator.connect()
        assert connected is True

        await communicator.send_json_to([3, completed_id, {"status": "Accepted"}])
        assert pending_id in store.pending_calls

        await communicator.send_json_to([3, pending_id, {"status": "Accepted"}])
        assert await _wait_for_pending_result(pending_id) is not None

        await communicator.disconnect()

    async_to_sync(run_scenario)()


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_unexpected_message_does_not_drop_restored_pending(fake_state_redis, temp_store_dirs):
    async def run_scenario():
        serial = "CP-UNEXPECTED"
        message_id = "unexpected-1"
        metadata = {
            "charger_id": serial,
            "action": "RemoteStopTransaction",
            "log_key": store.identity_key(serial, None),
        }
        store.register_pending_call(message_id, metadata)

        store.pending_calls.clear()
        store._pending_call_events.clear()
        store._pending_call_results.clear()
        store._pending_call_handles.clear()

        restored = store.restore_pending_calls(serial)
        assert message_id in restored
        assert message_id in store.pending_calls

        communicator = WebsocketCommunicator(application, f"/{serial}")
        connected, _ = await communicator.connect()
        assert connected is True

        await communicator.send_to(text_data='"garbled"')
        assert message_id in store.pending_calls

        await communicator.send_json_to([3, message_id, {"status": "Accepted"}])
        assert await _wait_for_pending_result(message_id) is not None

        await communicator.disconnect()

    async_to_sync(run_scenario)()
