import sys

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
        "ws://192.0.2.10/base/CP%2F42",
        "ws://192.0.2.10/base/ws/CP%2F42",
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

    from apps.ocpp import forwarder as forwarder_module, forwarding_utils

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
