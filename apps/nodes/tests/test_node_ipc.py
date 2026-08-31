from __future__ import annotations

import socket

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from apps.nodes.models import Node


@pytest.mark.django_db
def test_node_clean_rejects_relative_ipc_path(tmp_path):
    node = Node(
        hostname="node-a",
        public_endpoint="node-a",
        base_path=str(tmp_path),
        ipc_path="relative.sock",
    )

    with pytest.raises(ValidationError, match="IPC path must be absolute"):
        node.clean()


def test_node_same_host_prefers_host_instance_id_over_mac():
    node = Node(
        hostname="node-a",
        host_instance_id="host-a",
        mac_address="00:11:22:33:44:55",
    )
    other = Node(
        hostname="node-b",
        host_instance_id="host-b",
        mac_address="00:11:22:33:44:55",
    )

    assert node.is_same_host_as(other) is False


def test_node_same_host_falls_back_to_mac_when_host_id_missing():
    node = Node(hostname="node-a", mac_address="00:11:22:33:44:55")
    other = Node(hostname="node-b", mac_address="00:11:22:33:44:55")

    assert node.is_same_host_as(other) is True


def test_local_transport_status_reports_remote_host(tmp_path):
    node = Node(
        hostname="node-a",
        public_endpoint="node-a",
        base_path=str(tmp_path),
        host_instance_id="host-a",
    )
    local = Node(hostname="local", host_instance_id="host-b")

    status = node.get_local_transport_status(local_node=local)

    assert status["status"] == "remote_host"
    assert status["same_host"] is False
    assert status["ready"] is False


@pytest.mark.django_db
@override_settings(NODES_ENABLE_SIBLING_IPC=True)
def test_get_sibling_ipc_status_reports_wrong_type_for_non_socket(tmp_path):
    socket_path = tmp_path / "ipc" / "node-a.sock"
    socket_path.parent.mkdir(parents=True)
    socket_path.write_text("stub", encoding="utf-8")

    node = Node(
        hostname="node-a",
        public_endpoint="node-a",
        base_path=str(tmp_path),
    )

    status = node.get_sibling_ipc_status()

    assert status["status"] == "wrong_type"


@pytest.mark.django_db
@override_settings(NODES_ENABLE_SIBLING_IPC=True)
def test_get_sibling_ipc_status_reports_rejected_permissions(tmp_path):
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("UNIX sockets are not supported in this environment.")

    socket_path = tmp_path / "ipc" / "node-a.sock"
    socket_path.parent.mkdir(parents=True)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(socket_path))
    except PermissionError as exc:
        server.close()
        pytest.skip(f"UNIX socket bind is not permitted in this environment: {exc}")
    try:
        socket_path.chmod(0o666)
        node = Node(
            hostname="node-a",
            public_endpoint="node-a",
            base_path=str(tmp_path),
        )
        status = node.get_sibling_ipc_status()
    finally:
        server.close()

    assert status["status"] == "rejected_permissions"
