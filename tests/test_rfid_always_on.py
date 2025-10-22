import importlib
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


pytestmark = [pytest.mark.feature("rfid-scanner")]


@pytest.fixture
def always_on_module():
    module = importlib.import_module("ocpp.rfid.always_on")
    return importlib.reload(module)


def test_start_creates_thread_and_noop_when_running(monkeypatch, always_on_module):
    thread_instance = Mock()
    thread_instance.is_alive.return_value = False
    thread_factory = Mock(return_value=thread_instance)
    monkeypatch.setattr("ocpp.rfid.always_on.threading.Thread", thread_factory)

    always_on_module.start()

    thread_factory.assert_called_once()
    _, kwargs = thread_factory.call_args
    assert kwargs["target"] is always_on_module._worker
    assert kwargs["name"] == "rfid-watch"
    assert kwargs["daemon"] is True
    thread_instance.start.assert_called_once_with()

    thread_instance.is_alive.return_value = True
    always_on_module.start()

    thread_factory.assert_called_once()
    thread_instance.start.assert_called_once_with()


def test_worker_forwards_tag_payload(monkeypatch, always_on_module):
    stop_event = threading.Event()
    monkeypatch.setattr(always_on_module, "_stop", stop_event)

    send_mock = Mock()
    tag_signal = SimpleNamespace(send=send_mock)
    monkeypatch.setattr(always_on_module, "tag_scanned", tag_signal)

    def fake_get_next_tag(*, timeout):
        stop_event.set()
        return {"rfid": "card-123", "extra": "data"}

    get_next_tag_mock = Mock(side_effect=fake_get_next_tag)
    monkeypatch.setattr(always_on_module, "get_next_tag", get_next_tag_mock)

    always_on_module._worker()

    get_next_tag_mock.assert_called_with(timeout=0.1)
    send_mock.assert_called_once_with(sender=None, rfid="card-123", extra="data")


def test_stop_and_is_running_behaviour(monkeypatch, always_on_module):
    stop_event = Mock()
    thread_mock = Mock()
    monkeypatch.setattr(always_on_module, "_stop", stop_event)
    monkeypatch.setattr(always_on_module, "_thread", thread_mock)

    thread_mock.is_alive.return_value = True
    assert always_on_module.is_running() is True

    thread_mock.is_alive.return_value = False
    assert always_on_module.is_running() is False

    monkeypatch.setattr(always_on_module, "_thread", thread_mock)
    always_on_module.stop()

    stop_event.set.assert_called_once_with()
    thread_mock.join.assert_called_once_with(timeout=1)
