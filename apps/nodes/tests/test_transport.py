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

    def fake_ipc(
        *,
        socket_path: Path,
        managed_root: Path,
        operation: str,
        payload: dict[str, object],
    ):
        called.append(f"ipc:{operation}:{socket_path}")
        assert managed_root == tmp_path / "ipc"
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
def test_send_net_message_falls_back_to_http_when_ipc_unavailable(
    monkeypatch, tmp_path
):
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
def test_send_net_message_falls_back_to_http_when_ipc_rejects_payload(
    monkeypatch, tmp_path
):
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
def test_send_net_message_falls_back_to_http_when_socket_path_invalid(
    monkeypatch, tmp_path
):
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

    def fake_http(
        url: str, *, payload_json: str, headers: dict[str, str], timeout: float = 1.0
    ):
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


@pytest.mark.django_db
@override_settings(NODES_ENABLE_SIBLING_IPC=True)
def test_send_net_message_rejects_ipc_path_outside_node_root(monkeypatch, tmp_path):
    node = Node.objects.create(
        hostname="sibling-unsafe",
        public_endpoint="sibling-unsafe",
        base_path=str(tmp_path),
        ipc_path=str(tmp_path / "outside.sock"),
        current_relation=Node.Relation.SIBLING,
        address="127.0.0.1",
        port=8888,
    )

    def fail_ipc(**_kwargs):
        pytest.fail("unsafe IPC path should not be used")

    http_calls: list[str] = []
    monkeypatch.setattr(transport, "_request_via_unix_socket", fail_ipc)
    monkeypatch.setattr(
        transport,
        "_post_json",
        lambda url, **_kwargs: http_calls.append(url) or True,
    )

    ok = transport.send_net_message(
        {"sender": "abc", "subject": "test"},
        node,
        payload_json="{}",
        headers={"Content-Type": "application/json"},
    )

    assert ok is True
    assert http_calls


@pytest.mark.django_db
@override_settings(NODES_ENABLE_SIBLING_IPC=True)
def test_unix_socket_status_rejects_ipc_path_outside_node_root(tmp_path):
    node = Node.objects.create(
        hostname="sibling-status-unsafe",
        public_endpoint="sibling-status-unsafe",
        base_path=str(tmp_path),
        ipc_path=str(tmp_path / "outside.sock"),
        current_relation=Node.Relation.SIBLING,
    )

    status = transport.get_unix_socket_status(node)

    assert status["status"] == "rejected_path"
    assert status["ready"] is False


def test_resolve_managed_socket_path_accepts_absolute_child(tmp_path):
    managed_root = tmp_path / "ipc"
    socket_path = managed_root / "peer.sock"

    assert transport._resolve_managed_socket_path(socket_path, managed_root) == (
        tmp_path / "ipc" / "peer.sock"
    )


def test_resolve_managed_socket_path_preserves_relative_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    managed_root = Path("node") / "ipc"
    socket_path = managed_root / "peer.sock"

    assert transport._resolve_managed_socket_path(socket_path, managed_root) == (
        tmp_path / "node" / "ipc" / "peer.sock"
    )


def test_post_json_via_unix_socket_sends_http_request(monkeypatch, tmp_path):
    socket_path = tmp_path / "target.sock"
    sent_requests: list[bytes] = []

    class FakeSocket:
        def __init__(self):
            self.chunks = [
                b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{"status":"ok"}',
                b"",
            ]
            self.connected_path = ""
            self.timeout = None
            self.closed = False

        def settimeout(self, timeout):
            self.timeout = timeout

        def connect(self, path):
            self.connected_path = path

        def sendall(self, data):
            sent_requests.append(data)

        def recv(self, _size):
            return self.chunks.pop(0)

        def close(self):
            self.closed = True

    fake_socket = FakeSocket()
    monkeypatch.setattr(
        transport,
        "_get_unix_socket_path_status",
        lambda path, *, managed_root: {
            "transport": "unix_socket",
            "status": "ready",
            "ready": True,
            "configured": True,
            "path": str(path),
        },
    )
    monkeypatch.setattr(
        transport.socket,
        "socket",
        lambda family, kind: fake_socket,
    )
    monkeypatch.setattr(transport.socket, "AF_UNIX", object(), raising=False)

    response = transport.post_json_via_unix_socket(
        socket_path=socket_path,
        path="/nodes/network/chargers/import/",
        payload_json='{"ok":true}',
        headers={"Content-Type": "application/json", "X-Signature": "abc"},
        managed_root=tmp_path,
        timeout=2,
        host="target.local",
    )

    assert response == {"ok": True, "status_code": 200, "body": '{"status":"ok"}'}
    assert fake_socket.connected_path == str(socket_path)
    assert fake_socket.timeout == 2
    raw_request = sent_requests[0].decode("utf-8")
    assert raw_request.startswith("POST /nodes/network/chargers/import/ HTTP/1.1")
    assert "Host: target.local\r\n" in raw_request
    assert "X-Signature: abc\r\n" in raw_request


def test_post_json_via_unix_socket_connects_to_checked_path(monkeypatch, tmp_path):
    checked_socket_path = tmp_path / "target.sock"

    class FakeSocket:
        def __init__(self):
            self.chunks = [b"HTTP/1.1 204 No Content\r\n\r\n", b""]
            self.connected_path = ""

        def settimeout(self, timeout):
            pass

        def connect(self, path):
            self.connected_path = path

        def sendall(self, data):
            pass

        def recv(self, _size):
            return self.chunks.pop(0)

        def close(self):
            pass

    fake_socket = FakeSocket()
    monkeypatch.setattr(
        transport,
        "_get_unix_socket_path_status",
        lambda path, *, managed_root: {
            "status": "ready",
            "ready": True,
            "path": str(checked_socket_path),
        },
    )
    monkeypatch.setattr(transport.socket, "socket", lambda family, kind: fake_socket)
    monkeypatch.setattr(transport.socket, "AF_UNIX", object(), raising=False)

    response = transport.post_json_via_unix_socket(
        socket_path=Path("target.sock"),
        path="/nodes/network/chargers/import/",
        payload_json="{}",
        headers={},
        managed_root=tmp_path,
    )

    assert response["ok"] is True
    assert fake_socket.connected_path == str(checked_socket_path)


def test_post_json_via_unix_socket_rejects_host_header_injection(monkeypatch, tmp_path):
    socket_path = tmp_path / "target.sock"
    sent_requests: list[bytes] = []

    class FakeSocket:
        def __init__(self):
            self.chunks = [b"HTTP/1.1 204 No Content\r\n\r\n", b""]

        def settimeout(self, timeout):
            pass

        def connect(self, path):
            pass

        def sendall(self, data):
            sent_requests.append(data)

        def recv(self, _size):
            return self.chunks.pop(0)

        def close(self):
            pass

    monkeypatch.setattr(
        transport,
        "_get_unix_socket_path_status",
        lambda path, *, managed_root: {
            "status": "ready",
            "ready": True,
            "path": str(path),
        },
    )
    monkeypatch.setattr(transport.socket, "socket", lambda family, kind: FakeSocket())
    monkeypatch.setattr(transport.socket, "AF_UNIX", object(), raising=False)

    response = transport.post_json_via_unix_socket(
        socket_path=socket_path,
        path="/nodes/network/chargers/import/",
        payload_json='{"ok":true}',
        headers={"X-Bad": "kept-out\r\nX-Injected: yes"},
        managed_root=tmp_path,
        host="target.local\x1fmalformed",
    )

    assert response["ok"] is True
    raw_request = sent_requests[0].decode("utf-8")
    header_blob = raw_request.split("\r\n\r\n", 1)[0]
    assert "Host: localhost\r\n" in raw_request
    assert "X-Injected" not in header_blob


def test_post_json_via_unix_socket_quotes_request_path(monkeypatch, tmp_path):
    socket_path = tmp_path / "target.sock"
    sent_requests: list[bytes] = []

    class FakeSocket:
        def __init__(self):
            self.chunks = [b"HTTP/1.1 204 No Content\r\n\r\n", b""]

        def settimeout(self, timeout):
            pass

        def connect(self, path):
            pass

        def sendall(self, data):
            sent_requests.append(data)

        def recv(self, _size):
            return self.chunks.pop(0)

        def close(self):
            pass

    monkeypatch.setattr(
        transport,
        "_get_unix_socket_path_status",
        lambda path, *, managed_root: {
            "status": "ready",
            "ready": True,
            "path": str(path),
        },
    )
    monkeypatch.setattr(transport.socket, "socket", lambda family, kind: FakeSocket())
    monkeypatch.setattr(transport.socket, "AF_UNIX", object(), raising=False)

    response = transport.post_json_via_unix_socket(
        socket_path=socket_path,
        path="/nodes/network/chargers/import/\r\nX-Injected: yes",
        payload_json='{"ok":true}',
        headers={},
        managed_root=tmp_path,
        host="target.local",
    )

    assert response["ok"] is True
    raw_request = sent_requests[0].decode("utf-8")
    request_line = raw_request.split("\r\n", 1)[0]
    header_blob = raw_request.split("\r\n\r\n", 1)[0]
    assert request_line == (
        "POST /nodes/network/chargers/import/%0D%0AX-Injected%3A%20yes HTTP/1.1"
    )
    assert "X-Injected: yes" not in header_blob
