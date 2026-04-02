from __future__ import annotations

from pathlib import Path

import pytest
from django.test import override_settings

from apps.nodes.models import Node
from apps.nodes.services import transport


@pytest.mark.django_db
@override_settings(NODES_ENABLE_SIBLING_IPC=True)
def test_send_net_message_prefers_unix_socket_for_sibling(monkeypatch, tmp_path):
    node = Node.objects.create(
        hostname="sibling-a",
        public_endpoint="sibling-a",
        base_path=str(tmp_path),
        current_relation=Node.Relation.SIBLING,
    )

    called: list[str] = []

    def fake_ipc(*, socket_path: Path, operation: str, payload: dict[str, object]):
        called.append(f"ipc:{operation}:{socket_path}")
        return {"ok": True}

    def fail_http(*args, **kwargs):  # noqa: ANN002, ANN003
        pytest.fail("HTTP fallback should not run when unix socket succeeds")

    monkeypatch.setattr(transport, "_request_via_unix_socket", fake_ipc)
    monkeypatch.setattr(transport, "_post_json", fail_http)

    ok = transport.send_net_message(
        {"sender": "abc", "subject": "test"},
        node,
        payload_json="{}",
        headers={"Content-Type": "application/json"},
    )

    assert ok is True
    assert called


@pytest.mark.django_db
@override_settings(NODES_ENABLE_SIBLING_IPC=True)
def test_send_net_message_falls_back_to_http_when_ipc_unavailable(monkeypatch, tmp_path):
    node = Node.objects.create(
        hostname="sibling-b",
        public_endpoint="sibling-b",
        base_path=str(tmp_path),
        current_relation=Node.Relation.SIBLING,
        address="127.0.0.1",
        port=8888,
    )

    monkeypatch.setattr(
        transport,
        "_request_via_unix_socket",
        lambda **kwargs: (_ for _ in ()).throw(transport.TransportError("missing")),
    )
    monkeypatch.setattr(transport, "_post_json", lambda *args, **kwargs: True)

    ok = transport.send_net_message(
        {"sender": "abc", "subject": "test"},
        node,
        payload_json="{}",
        headers={"Content-Type": "application/json"},
    )

    assert ok is True


@pytest.mark.django_db
@override_settings(NODES_ENABLE_SIBLING_IPC=True)
def test_send_net_message_falls_back_to_http_when_ipc_rejects_payload(monkeypatch, tmp_path):
    node = Node.objects.create(
        hostname="sibling-c",
        public_endpoint="sibling-c",
        base_path=str(tmp_path),
        current_relation=Node.Relation.SIBLING,
        address="127.0.0.1",
        port=8888,
    )

    monkeypatch.setattr(
        transport,
        "_request_via_unix_socket",
        lambda **kwargs: {"ok": False, "detail": "sibling relation required"},
    )
    monkeypatch.setattr(transport, "_post_json", lambda *args, **kwargs: True)

    ok = transport.send_net_message(
        {"sender": "abc", "subject": "test"},
        node,
        payload_json="{}",
        headers={"Content-Type": "application/json"},
    )

    assert ok is True


@pytest.mark.django_db
@override_settings(NODES_ENABLE_SIBLING_IPC=True)
def test_send_net_message_falls_back_to_http_when_socket_path_invalid(monkeypatch, tmp_path):
    socket_path = tmp_path / "ipc" / "sibling-c.sock"
    socket_path.parent.mkdir(parents=True)
    socket_path.write_text("stub", encoding="utf-8")  # Not a valid UNIX socket.
    socket_path.chmod(0o666)

    node = Node.objects.create(
        hostname="sibling-c",
        public_endpoint="sibling-c",
        base_path=str(tmp_path),
        current_relation=Node.Relation.SIBLING,
        address="127.0.0.1",
        port=8888,
    )

    http_calls: list[str] = []

    def fake_http(url: str, *, payload_json: str, headers: dict[str, str], timeout: float = 1.0):
        http_calls.append(url)
        return False

    monkeypatch.setattr(transport, "_post_json", fake_http)

    ok = transport.send_net_message(
        {"sender": "abc", "subject": "test"},
        node,
        payload_json="{}",
        headers={"Content-Type": "application/json"},
    )

    assert ok is False
    assert http_calls
