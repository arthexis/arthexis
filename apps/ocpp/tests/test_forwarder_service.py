import asyncio
import logging
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import websockets

from django.utils import timezone

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


def test_connect_forwarding_session_handles_failures(
    monkeypatch, forwarder_instance, caplog
):
    charger = SimpleNamespace(pk=1, charger_id="CP-1")
    node = SimpleNamespace(iter_remote_urls=lambda path: [
        "http://unreliable.example.com/",
        "http://reliable.example.com/",
    ])

    async def orchestrate():
        async def reject_request(_path, _headers):
            return HTTPStatus.FORBIDDEN, [], b"nope"

        async def echo_handler(websocket):
            async for message in websocket:
                await websocket.send(message)

        async with (
            websockets.serve(
                echo_handler,
                "localhost",
                0,
                subprotocols=["ocpp1.6"],
                process_request=reject_request,
            ) as abort_server,
            websockets.serve(
                echo_handler, "localhost", 0, subprotocols=["ocpp1.6"]
            ) as live_server,
        ):
            failing_url = f"ws://localhost:{abort_server.sockets[0].getsockname()[1]}"
            live_url = f"ws://localhost:{live_server.sockets[0].getsockname()[1]}"

            monkeypatch.setattr(
                Forwarder,
                "_candidate_forwarding_urls",
                staticmethod(lambda _node, _charger: iter([failing_url, live_url])),
            )

            caplog.set_level(logging.WARNING)

            session = await asyncio.to_thread(
                forwarder_instance.connect_forwarding_session,
                charger,
                node,
                timeout=0.5,
            )

            assert session is not None
            assert session.url == live_url
            assert forwarder_instance.get_session(charger.pk) is session
            assert len(forwarder_instance._sessions) == 1
            assert any(
                failing_url in record.message and record.levelno == logging.WARNING
                for record in caplog.records
            )

            # verify failures leave no sessions behind when nothing connects
            forwarder_instance.clear_sessions()
            monkeypatch.setattr(
                Forwarder,
                "_candidate_forwarding_urls",
                staticmethod(lambda _node, _charger: iter([failing_url])),
            )

            caplog.clear()
            session = await asyncio.to_thread(
                forwarder_instance.connect_forwarding_session,
                charger,
                node,
                timeout=0.5,
            )
            assert session is None
            assert forwarder_instance.get_session(charger.pk) is None
            assert any(
                failing_url in record.message and record.levelno == logging.WARNING
                for record in caplog.records
            )

    asyncio.run(orchestrate())


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
    create_conn = Mock()
    monkeypatch.setattr("apps.ocpp.forwarder.create_connection", create_conn)

    forwarder.sync_forwarded_charge_points()

    assert existing_session.forwarder_id == cp_forwarder.pk
    assert existing_session.forwarded_messages == tuple(cp_forwarder.get_forwarded_messages())
    create_conn.assert_not_called()
    update_running_state.assert_called_once_with({target.pk})
    assert forwarder.get_session(charger.pk) is existing_session

