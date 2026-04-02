"""Tests for CSMS transport forwarding replies to legacy forwarder sessions."""

from __future__ import annotations

import json
from datetime import timedelta
from threading import Lock
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from django.utils import timezone

from apps.ocpp.consumers.csms.transport import CSMSTransportMixin


class DummyTransport(CSMSTransportMixin):
    """Small harness for testing forwarding helpers without full consumer setup."""


class FakeSession:
    """Minimal forwarding session representation used by transport tests."""

    def __init__(self, *, pending_call_ids: set[str], is_connected: bool = True, send=None) -> None:
        self.pending_call_ids = pending_call_ids
        self.is_connected = is_connected
        self.connection = SimpleNamespace(send=send or Mock())
        self.forwarded_messages = ("Heartbeat", "StatusNotification")
        self.forwarder_id = 1
        self.forwarding_interval_seconds = 0.0
        self.pending_cp_messages = {}
        self.last_cp_flush_at = None
        self._pending_lock = Lock()
        self._cp_messages_lock = Lock()
        self.url = "ws://forwarder.test"
        self.last_activity = None


@pytest.mark.anyio
async def test_forward_charge_point_reply_sends_and_clears_pending_id(monkeypatch):
    """Replies with pending message IDs should be forwarded and de-queued."""

    transport = DummyTransport()
    transport.aggregate_charger = None
    transport.charger = SimpleNamespace(pk=10, charger_id="CP-10", connector_id=1)
    session = FakeSession(pending_call_ids={"msg-1"})

    fake_forwarder = SimpleNamespace(get_session=Mock(return_value=session), remove_session=Mock())
    monkeypatch.setattr("apps.ocpp.consumers.csms.transport.forwarder", fake_forwarder)
    monkeypatch.setattr("apps.ocpp.consumers.csms.transport.ocpp_forwarder_enabled", lambda default=True: True)
    monkeypatch.setattr("apps.ocpp.consumers.csms.transport.Node.get_local", Mock(return_value=None))

    raw = '[3,"msg-1",{"status":"Accepted"}]'
    await transport._forward_charge_point_reply_legacy("msg-1", raw)

    session.connection.send.assert_called_once()
    forwarded_message = session.connection.send.call_args.args[0]
    wrapped = json.loads(forwarded_message)
    assert wrapped["meta"]["direction"] == "cp_to_csms_reply"
    assert "msg-1" not in session.pending_call_ids


@pytest.mark.anyio
async def test_forward_charge_point_reply_noops_for_non_pending_message_id(monkeypatch):
    """Replies not tracked as pending should not be forwarded or mutated."""

    transport = DummyTransport()
    transport.aggregate_charger = None
    transport.charger = SimpleNamespace(pk=11, charger_id="CP-11", connector_id=2)
    session = FakeSession(pending_call_ids={"msg-keep"})

    fake_forwarder = SimpleNamespace(get_session=Mock(return_value=session), remove_session=Mock())
    monkeypatch.setattr("apps.ocpp.consumers.csms.transport.forwarder", fake_forwarder)
    monkeypatch.setattr("apps.ocpp.consumers.csms.transport.ocpp_forwarder_enabled", lambda default=True: True)

    await transport._forward_charge_point_reply_legacy("msg-missing", '[3,"msg-missing",{}]')

    session.connection.send.assert_not_called()
    assert session.pending_call_ids == {"msg-keep"}
    fake_forwarder.remove_session.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "session",
    [None, FakeSession(pending_call_ids={"msg-2"}, is_connected=False)],
    ids=["missing-session", "disconnected-session"],
)
async def test_forward_charge_point_reply_noops_for_missing_or_disconnected_session(
    monkeypatch, session
):
    """Missing or disconnected sessions should skip forwarding without side effects."""

    transport = DummyTransport()
    transport.aggregate_charger = None
    transport.charger = SimpleNamespace(pk=12, charger_id="CP-12", connector_id=3)

    fake_forwarder = SimpleNamespace(get_session=Mock(return_value=session), remove_session=Mock())
    monkeypatch.setattr("apps.ocpp.consumers.csms.transport.forwarder", fake_forwarder)
    monkeypatch.setattr("apps.ocpp.consumers.csms.transport.ocpp_forwarder_enabled", lambda default=True: True)

    await transport._forward_charge_point_reply_legacy("msg-2", '[3,"msg-2",{}]')

    fake_forwarder.get_session.assert_called_once_with(12)
    fake_forwarder.remove_session.assert_not_called()
    if session is not None:
        session.connection.send.assert_not_called()
        assert session.pending_call_ids == {"msg-2"}


@pytest.mark.anyio
async def test_forward_charge_point_reply_removes_session_when_send_fails(monkeypatch):
    """Forwarder should drop the session when reply forwarding raises an exception."""

    transport = DummyTransport()
    transport.aggregate_charger = None
    transport.charger = SimpleNamespace(pk=13, charger_id="CP-13", connector_id=4)

    def raise_send(_payload: str) -> None:
        raise RuntimeError("send failure")

    session = FakeSession(pending_call_ids={"msg-3"}, send=raise_send)
    fake_forwarder = SimpleNamespace(get_session=Mock(return_value=session), remove_session=Mock())

    monkeypatch.setattr("apps.ocpp.consumers.csms.transport.forwarder", fake_forwarder)
    monkeypatch.setattr("apps.ocpp.consumers.csms.transport.ocpp_forwarder_enabled", lambda default=True: True)

    await transport._forward_charge_point_reply_legacy("msg-3", '[3,"msg-3",{}]')

    fake_forwarder.remove_session.assert_called_once_with(13)
    assert "msg-3" not in session.pending_call_ids


@pytest.mark.anyio
async def test_forward_charge_point_message_sends_immediately_when_not_throttled(monkeypatch):
    """Forwarding without a frequency limit should send each message immediately."""

    transport = DummyTransport()
    transport.aggregate_charger = None
    transport.charger = SimpleNamespace(pk=14, charger_id="CP-14", connector_id=1, forwarded_to_id=None)
    transport._record_forwarding_activity = AsyncMock()
    session = FakeSession(pending_call_ids=set())

    fake_forwarder = SimpleNamespace(get_session=Mock(return_value=session), remove_session=Mock())
    monkeypatch.setattr("apps.ocpp.consumers.csms.transport.forwarder", fake_forwarder)
    monkeypatch.setattr("apps.ocpp.consumers.csms.transport.ocpp_forwarder_enabled", lambda default=True: True)
    monkeypatch.setattr("apps.ocpp.consumers.csms.transport.Node.get_local", Mock(return_value=None))

    await transport._forward_charge_point_message_legacy("Heartbeat", '[2,"m-1","Heartbeat",{}]')

    session.connection.send.assert_called_once()
    transport._record_forwarding_activity.assert_called_once()


@pytest.mark.anyio
async def test_forward_charge_point_message_throttles_and_keeps_latest_per_action(monkeypatch):
    """Throttled forwarding should keep only the latest payload for each action."""

    transport = DummyTransport()
    transport.aggregate_charger = None
    transport.charger = SimpleNamespace(pk=15, charger_id="CP-15", connector_id=1, forwarded_to_id=None)
    transport._record_forwarding_activity = AsyncMock()
    session = FakeSession(pending_call_ids=set())
    session.forwarding_interval_seconds = 10.0

    fake_forwarder = SimpleNamespace(get_session=Mock(return_value=session), remove_session=Mock())
    monkeypatch.setattr("apps.ocpp.consumers.csms.transport.forwarder", fake_forwarder)
    monkeypatch.setattr("apps.ocpp.consumers.csms.transport.ocpp_forwarder_enabled", lambda default=True: True)
    monkeypatch.setattr("apps.ocpp.consumers.csms.transport.Node.get_local", Mock(return_value=None))

    now = timezone.now()
    monkeypatch.setattr(
        "apps.ocpp.consumers.csms.transport.timezone.now",
        Mock(
            side_effect=[
                now,
                now,
                now + timedelta(seconds=1),
                now + timedelta(seconds=11),
                now + timedelta(seconds=11),
            ]
        ),
    )

    await transport._forward_charge_point_message_legacy("Heartbeat", '[2,"m-1","Heartbeat",{"value":"old"}]')
    await transport._forward_charge_point_message_legacy("Heartbeat", '[2,"m-2","Heartbeat",{"value":"new"}]')
    await transport._forward_charge_point_message_legacy("StatusNotification", '[2,"m-3","StatusNotification",{}]')

    assert session.connection.send.call_count == 3
    forwarded_ids = [
        json.loads(call.args[0])["ocpp"][1]
        for call in session.connection.send.call_args_list
    ]
    assert forwarded_ids == ["m-1", "m-2", "m-3"]
    assert transport._record_forwarding_activity.call_count == 2
