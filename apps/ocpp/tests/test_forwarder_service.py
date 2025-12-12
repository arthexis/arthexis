import pytest
from types import SimpleNamespace
from unittest.mock import Mock

from django.utils import timezone

from websocket import WebSocketException

from apps.ocpp.forwarder import Forwarder, ForwardingSession
from apps.ocpp.models import CPForwarder, Charger
from apps.nodes.models import Node


@pytest.fixture
def forwarder_instance():
    return Forwarder()


def test_candidate_forwarding_urls_builds_ws_and_wss(forwarder_instance):
    node = SimpleNamespace(
        iter_remote_urls=lambda path: [
            "http://example.com/base/",
            "https://secure.example.com/root",
            "ftp://ignored.example.com/",
        ]
    )
    charger = SimpleNamespace(charger_id="CP/42")

    urls = list(forwarder_instance._candidate_forwarding_urls(node, charger))

    assert urls == [
        "ws://example.com/base/CP%2F42",
        "ws://example.com/base/ws/CP%2F42",
        "wss://secure.example.com/root/CP%2F42",
        "wss://secure.example.com/root/ws/CP%2F42",
    ]


def test_connect_forwarding_session_handles_failures(monkeypatch, forwarder_instance):
    charger = SimpleNamespace(pk=1, charger_id="CP-1")
    node = SimpleNamespace(iter_remote_urls=lambda path: [
        "http://unreliable.example.com/",
        "http://reliable.example.com/",
    ])

    connections = []

    def fake_connect(url, timeout, subprotocols):
        connections.append(url)
        if "unreliable" in url:
            raise WebSocketException("boom")
        return SimpleNamespace(connected=True, close=Mock())

    monkeypatch.setattr("apps.ocpp.forwarder.create_connection", fake_connect)
    monkeypatch.setattr(
        "apps.ocpp.forwarder.logger", SimpleNamespace(warning=Mock(), info=Mock())
    )

    session = forwarder_instance.connect_forwarding_session(charger, node, timeout=0.1)

    assert session is not None
    assert session.url.startswith("ws://reliable.example.com")
    assert forwarder_instance.get_session(charger.pk) is session
    assert len(forwarder_instance._sessions) == 1
    assert connections[0].startswith("ws://unreliable.example.com")

    # verify failures leave no sessions behind when nothing connects
    def always_fail(url, timeout, subprotocols):
        raise WebSocketException("down")

    monkeypatch.setattr("apps.ocpp.forwarder.create_connection", always_fail)
    forwarder_instance.clear_sessions()
    session = forwarder_instance.connect_forwarding_session(charger, node, timeout=0.1)
    assert session is None
    assert forwarder_instance.get_session(charger.pk) is None


def test_prune_inactive_sessions_closes_missing(monkeypatch, forwarder_instance):
    active_connection = SimpleNamespace(connected=True, close=Mock())
    stale_connection = SimpleNamespace(connected=True, close=Mock())

    forwarder_instance._sessions = {
        1: ForwardingSession(
            charger_pk=1,
            node_id=10,
            url="ws://one",
            connection=active_connection,
            connected_at=timezone.now(),
        ),
        2: ForwardingSession(
            charger_pk=2,
            node_id=20,
            url="ws://two",
            connection=stale_connection,
            connected_at=timezone.now(),
        ),
    }

    forwarder_instance.prune_inactive_sessions([1])

    assert 1 in forwarder_instance._sessions
    assert 2 not in forwarder_instance._sessions
    stale_connection.close.assert_called_once()


@pytest.mark.django_db
def test_sync_forwarded_charge_points_respects_existing_sessions(monkeypatch):
    forwarder = Forwarder()

    local = Node.objects.create(hostname="local", mac_address="00:11:22:33:44:55")
    target = Node.objects.create(hostname="remote", mac_address="66:77:88:99:AA:BB")
    monkeypatch.setattr(target, "iter_remote_urls", lambda path: ["http://remote/ws"])
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: local))

    monkeypatch.setattr(CPForwarder, "sync_chargers", lambda self, apply_sessions=True: None)

    cp_forwarder = CPForwarder.objects.create(
        target_node=target,
        enabled=True,
        forwarded_messages=["BootNotification"],
    )
    charger = Charger.objects.create(
        charger_id="CP-100",
        export_transactions=True,
        forwarded_to=target,
        node_origin=local,
    )

    connection = SimpleNamespace(connected=True, close=Mock())
    existing_session = ForwardingSession(
        charger_pk=charger.pk,
        node_id=target.pk,
        url="ws://existing",
        connection=connection,
        connected_at=timezone.now(),
    )
    forwarder._sessions[charger.pk] = existing_session

    update_running_state = Mock()
    sync_forwarding_targets = Mock()
    monkeypatch.setattr(CPForwarder.objects, "update_running_state", update_running_state)
    monkeypatch.setattr(CPForwarder.objects, "sync_forwarding_targets", sync_forwarding_targets)

    fake_logger = SimpleNamespace(warning=Mock(), info=Mock())
    monkeypatch.setattr("apps.ocpp.forwarder.logger", fake_logger)
    create_conn = Mock()
    monkeypatch.setattr("apps.ocpp.forwarder.create_connection", create_conn)

    forwarder.sync_forwarded_charge_points()

    assert existing_session.forwarder_id == cp_forwarder.pk
    assert existing_session.forwarded_messages == tuple(cp_forwarder.get_forwarded_messages())
    create_conn.assert_not_called()
    update_running_state.assert_called_once_with({target.pk})
    assert forwarder.get_session(charger.pk) is existing_session

