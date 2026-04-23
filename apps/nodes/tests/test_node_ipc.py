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


@pytest.mark.django_db
def test_node_clean_rejects_ipc_path_outside_managed_directory(tmp_path):
    node = Node(
        hostname="node-a",
        public_endpoint="node-a",
        base_path=str(tmp_path),
        ipc_path="/tmp/outside.sock",
    )

    with pytest.raises(ValidationError, match="IPC path must be within"):
        node.clean()


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
