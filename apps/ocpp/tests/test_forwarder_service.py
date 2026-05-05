import json
import sys
from datetime import timedelta

import pytest
from types import SimpleNamespace
from unittest.mock import Mock

from django.utils import timezone

from websocket import WebSocketException

from apps.ocpp.forwarder import Forwarder, ForwardingSession
from apps.ocpp.services import health_checks
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
        "ws://example.com/base/ocpp/CP%2F42",
        "ws://example.com/base/ws/ocpp/CP%2F42",
        "ws://example.com/base/CP%2F42",
        "ws://example.com/base/ws/CP%2F42",
        "wss://secure.example.com/root/ocpp/CP%2F42",
        "wss://secure.example.com/root/ws/ocpp/CP%2F42",
        "wss://secure.example.com/root/CP%2F42",
        "wss://secure.example.com/root/ws/CP%2F42",
    ]


def test_candidate_forwarding_urls_skips_tls_ip_targets(forwarder_instance):
    node = SimpleNamespace(
        iter_remote_urls=lambda path: [
            "https://192.0.2.10/base/",
            "http://192.0.2.10/base/",
        ]
    )
    charger = SimpleNamespace(charger_id="CP/42")

    urls = list(forwarder_instance._candidate_forwarding_urls(node, charger))

    assert urls == [
        "ws://192.0.2.10/base/ocpp/CP%2F42",
        "ws://192.0.2.10/base/ws/ocpp/CP%2F42",
        "ws://192.0.2.10/base/CP%2F42",
        "ws://192.0.2.10/base/ws/CP%2F42",
    ]


@pytest.mark.django_db
def test_run_check_forwarders_reports_current_and_legacy_websocket_paths(monkeypatch):
    class Stdout:
        def __init__(self):
            self.lines: list[str] = []

        def write(self, message: str) -> None:
            self.lines.append(message)

    local = SimpleNamespace(
        public_endpoint="example.test",
        public_key="public-key",
        get_remote_host_candidates=lambda resolve_dns=False: ["example.test"],
        iter_remote_urls=lambda path: [f"http://example.test{path}"],
        __str__=lambda self: "local",
    )
    monkeypatch.setattr(health_checks.Node, "get_local", staticmethod(lambda: local))
    monkeypatch.setattr(
        health_checks,
        "build_nginx_report",
        lambda: {"mode": "", "port": "", "actual_path": "", "differs": False},
    )
    stdout = Stdout()

    health_checks.run_check_forwarders(stdout=stdout)

    current_line = next(
        line
        for line in stdout.lines
        if line.startswith("  OCPP websocket endpoints: ")
    )
    legacy_line = next(
        line
        for line in stdout.lines
        if line.startswith("  OCPP websocket endpoints (legacy / and /ws): ")
    )
    assert "ws://example.test/ocpp/<charger_id>" in current_line
    assert "ws://example.test/ws/ocpp/<charger_id>" in current_line
    assert "ws://example.test/ws/<charger_id>" in legacy_line
    assert "ws://example.test/<charger_id>" in legacy_line



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


def test_keepalive_sessions_pings_idle_connections(forwarder_instance):
    connection = SimpleNamespace(connected=True, close=Mock(), ping=Mock())
    session = ForwardingSession(
        charger_pk=1,
        node_id=10,
        url="ws://one",
        connection=connection,
        connected_at=timezone.now() - timedelta(minutes=5),
        last_activity=timezone.now() - timedelta(minutes=5),
    )
    forwarder_instance._sessions = {1: session}

    pinged = forwarder_instance.keepalive_sessions(idle_seconds=60)

    assert pinged == 1
    connection.ping.assert_called_once()
    assert forwarder_instance.get_session(1) is session


def test_keepalive_sessions_removes_dead_connections(forwarder_instance):
    connection = SimpleNamespace(
        connected=True,
        close=Mock(),
        ping=Mock(side_effect=WebSocketException("closed")),
    )
    session = ForwardingSession(
        charger_pk=1,
        node_id=10,
        url="ws://one",
        connection=connection,
        connected_at=timezone.now() - timedelta(minutes=5),
        last_activity=timezone.now() - timedelta(minutes=5),
    )
    forwarder_instance._sessions = {1: session}

    pinged = forwarder_instance.keepalive_sessions(idle_seconds=60)

    assert pinged == 0
    assert forwarder_instance.get_session(1) is None
    connection.close.assert_called_once()
@pytest.mark.django_db
def test_sync_forwarded_charge_points_respects_existing_sessions(monkeypatch):
    forwarder = Forwarder()

    mac_address = "00:11:22:33:44:55"
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: mac_address))
    Node._local_cache.clear()

    attempted_urls: list[str] = []
    accepted_urls: set[str] = set()

    def fake_create_connection(url, timeout, subprotocols):
        attempted_urls.append(url)
        if url in accepted_urls:
            return SimpleNamespace(connected=True, close=Mock())
        raise WebSocketException("reject")

    fake_logger = SimpleNamespace(warning=Mock(), info=Mock())
    monkeypatch.setattr("apps.ocpp.forwarder.logger", fake_logger)
    monkeypatch.setattr("apps.ocpp.forwarder.create_connection", fake_create_connection)

    from apps.ocpp import forwarder as forwarder_module
    from apps.ocpp import forwarding_utils

    monkeypatch.setitem(sys.modules, "apps.ocpp.models.forwarder", forwarder_module)

    local = Node.objects.create(hostname="local", mac_address=mac_address)
    target = Node.objects.create(hostname="remote", mac_address="66:77:88:99:AA:BB")

    monkeypatch.setattr(
        forwarding_utils, "load_local_node_credentials", lambda: (local, None, "")
    )
    monkeypatch.setattr(forwarding_utils, "attempt_forwarding_probe", lambda *_, **__: False)
    monkeypatch.setattr(
        forwarding_utils, "send_forwarding_metadata", lambda *_, **__: (True, None)
    )

    cp_forwarder = CPForwarder(
        target_node=target,
        enabled=True,
        forwarded_messages=["BootNotification"],
    )
    cp_forwarder.save(sync_chargers=False)
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

    target_two = Node.objects.create(
        hostname="remote-2",
        mac_address="11:22:33:44:55:66",
    )

    def iter_remote_urls(node, path):
        if getattr(node, "hostname", None) == "remote-2":
            return ["http://remote-2/ws"]
        if getattr(node, "hostname", None) == "remote":
            return ["http://remote/ws"]
        return []

    monkeypatch.setattr(Node, "iter_remote_urls", iter_remote_urls)
    cp_forwarder_two = CPForwarder(
        target_node=target_two,
        enabled=True,
        forwarded_messages=["Heartbeat"],
    )
    cp_forwarder_two.save(sync_chargers=False)

    accepted_urls.update(
        Forwarder._candidate_forwarding_urls(target_two, charger)  # type: ignore[arg-type]
    )

    forwarder.sync_forwarded_charge_points()

    assert forwarder.get_session(charger.pk) is existing_session
    assert attempted_urls == []
    assert existing_session.forwarder_id == cp_forwarder.pk
    assert existing_session.forwarded_messages == tuple(cp_forwarder.get_forwarded_messages())
    assert CPForwarder.objects.get(pk=cp_forwarder.pk).is_running is True

    charger.forwarded_to = target_two
    charger.save(update_fields=["forwarded_to"])

    forwarder.sync_forwarded_charge_points()

    new_session = forwarder.get_session(charger.pk)
    assert new_session is not None
    assert new_session is not existing_session
    assert any(url in accepted_urls for url in attempted_urls)
    assert new_session.node_id == target_two.pk
    assert new_session.forwarder_id == cp_forwarder_two.pk
    assert new_session.forwarded_messages == tuple(
        cp_forwarder_two.get_forwarded_messages()
    )

    assert CPForwarder.objects.get(pk=cp_forwarder.pk).is_running is False
    assert CPForwarder.objects.get(pk=cp_forwarder_two.pk).is_running is True


@pytest.mark.django_db
def test_sync_forwarded_charge_points_dedupes_charger_ids(monkeypatch):
    """Ensure only one forwarding session is created per charger identifier."""

    forwarder = Forwarder()
    mac_address = "00:11:22:33:44:55"
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: mac_address))
    Node._local_cache.clear()

    local = Node.objects.create(hostname="local", mac_address=mac_address)
    target = Node.objects.create(hostname="remote", mac_address="66:77:88:99:AA:BB")
    cp_forwarder = CPForwarder.objects.create(target_node=target, enabled=True)

    charger_primary = Charger.objects.create(
        charger_id="CP-200",
        connector_id=None,
        export_transactions=True,
        forwarded_to=target,
        node_origin=local,
    )
    Charger.objects.create(
        charger_id="CP-200",
        connector_id=1,
        export_transactions=True,
        forwarded_to=target,
        node_origin=local,
    )
    Charger.objects.create(
        charger_id="CP-200",
        connector_id=2,
        export_transactions=True,
        forwarded_to=target,
        node_origin=local,
    )

    monkeypatch.setattr(Node, "iter_remote_urls", lambda *_: ["http://remote/ws"])

    connections = []

    def fake_create_connection(url, timeout, subprotocols):
        connection = SimpleNamespace(connected=True, close=Mock())
        connections.append(connection)
        return connection

    monkeypatch.setattr("apps.ocpp.forwarder.create_connection", fake_create_connection)

    connected = forwarder.sync_forwarded_charge_points(refresh_forwarders=False)

    assert connected == 1
    assert len(connections) == 1
    assert forwarder.get_session(charger_primary.pk) is not None
    assert CPForwarder.objects.get(pk=cp_forwarder.pk).is_running is True


def test_listener_does_not_drop_reconnected_session(forwarder_instance):
    old_connection = SimpleNamespace(close=Mock())
    new_connection = SimpleNamespace(connected=True, close=Mock())

    old_session = ForwardingSession(
        charger_pk=42,
        node_id=100,
        url="ws://old",
        connection=old_connection,
        connected_at=timezone.now(),
    )
    new_session = ForwardingSession(
        charger_pk=42,
        node_id=100,
        url="ws://new",
        connection=new_connection,
        connected_at=timezone.now(),
    )
    forwarder_instance._sessions[42] = old_session

    old_connection.connected = True
    old_connection.send = Mock()

    def recv_with_reconnect():
        forwarder_instance._sessions[42] = new_session
        raise RuntimeError("socket reset")

    old_connection.recv = Mock(side_effect=recv_with_reconnect)

    forwarder_instance._listen_forwarding_session(old_session)

    assert forwarder_instance.get_session(42) is new_session
    old_connection.close.assert_called_once()


@pytest.mark.django_db
def test_listener_rolls_back_pending_call_when_send_to_cp_fails(monkeypatch, forwarder_instance):
    charger = Charger.objects.create(charger_id="CP-ROLLBACK", allow_remote=True)
    session_connection = SimpleNamespace(
        connected=True,
        close=Mock(),
        send=Mock(),
    )
    command = json.dumps([2, "msg-rollback", "Heartbeat", {}])
    session_connection.recv = Mock(side_effect=[command, RuntimeError("done")])

    session = ForwardingSession(
        charger_pk=charger.pk,
        node_id=100,
        url="ws://remote",
        connection=session_connection,
        connected_at=timezone.now(),
        forwarded_calls=("Heartbeat",),
    )
    forwarder_instance._sessions[charger.pk] = session

    pop_pending_call = Mock()
    register_pending_call = Mock()
    from apps.ocpp import store

    monkeypatch.setattr(store, "add_log", Mock())
    monkeypatch.setattr(
        store,
        "get_connection",
        Mock(return_value=SimpleNamespace(send=Mock(side_effect=RuntimeError("fail")))),
    )
    monkeypatch.setattr(store, "identity_key", Mock(return_value="cp-rollback-1"))
    monkeypatch.setattr(store, "pop_pending_call", pop_pending_call)
    monkeypatch.setattr(store, "register_pending_call", register_pending_call)
    monkeypatch.setattr("asgiref.sync.async_to_sync", lambda fn: fn)

    forwarder_instance._listen_forwarding_session(session)

    register_pending_call.assert_called_once()
    pop_pending_call.assert_called_once_with("msg-rollback")


@pytest.mark.django_db
def test_sync_forwarded_charge_points_removes_sessions_forwarded_back_to_local(monkeypatch):
    forwarder = Forwarder()

    mac_address = "00:11:22:33:44:55"
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: mac_address))
    Node._local_cache.clear()

    local = Node.objects.create(hostname="local", mac_address=mac_address)
    charger = Charger.objects.create(
        charger_id="CP-LOCAL",
        export_transactions=True,
        forwarded_to=local,
        node_origin=local,
    )
    existing = ForwardingSession(
        charger_pk=charger.pk,
        node_id=local.pk,
        url="ws://stale",
        connection=SimpleNamespace(connected=True, close=Mock()),
        connected_at=timezone.now(),
    )
    forwarder._sessions[charger.pk] = existing

    connected = forwarder.sync_forwarded_charge_points(refresh_forwarders=False)

    assert connected == 0
    assert forwarder.get_session(charger.pk) is None
    existing.connection.close.assert_called_once()


@pytest.mark.django_db
def test_sync_forwarded_charge_points_flushes_buffer_when_interval_switches_to_immediate(monkeypatch):
    """Switching to immediate forwarding should flush any buffered throttled payloads."""

    forwarder = Forwarder()
    mac_address = "00:11:22:33:44:66"
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: mac_address))
    Node._local_cache.clear()

    local = Node.objects.create(hostname="local-interval", mac_address=mac_address)
    target = Node.objects.create(hostname="remote-interval", mac_address="66:77:88:00:AA:BB")
    charger = Charger.objects.create(
        charger_id="CP-INTERVAL",
        export_transactions=True,
        forwarded_to=target,
        node_origin=local,
    )
    cp_forwarder = CPForwarder.objects.create(
        target_node=target,
        enabled=True,
        forwarding_frequency_hz=1.0,
    )

    connection = SimpleNamespace(connected=True, close=Mock(), send=Mock())
    existing = ForwardingSession(
        charger_pk=charger.pk,
        node_id=target.pk,
        url="ws://existing",
        connection=connection,
        connected_at=timezone.now(),
        forwarder_id=cp_forwarder.pk,
        forwarding_interval_seconds=1.0,
    )
    existing.pending_cp_messages = {"Heartbeat": '{"ocpp":[2,"m-1","Heartbeat",{}]}'}
    forwarder._sessions[charger.pk] = existing

    cp_forwarder.forwarding_frequency_hz = 0.0
    cp_forwarder.save(sync_chargers=False)

    monkeypatch.setattr(
        "apps.ocpp.forwarder.create_connection",
        lambda *_args, **_kwargs: SimpleNamespace(connected=True, close=Mock(), send=Mock()),
    )

    forwarder.sync_forwarded_charge_points(refresh_forwarders=False)

    assert forwarder.get_session(charger.pk) is existing
    connection.send.assert_called_once_with('{"ocpp":[2,"m-1","Heartbeat",{}]}')
    assert existing.pending_cp_messages == {}
    assert existing.forwarding_interval_seconds == 0.0
