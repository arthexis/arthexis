import base64
import json

import pytest
from django.core.management import get_commands, load_command_class
from django.core.management.base import CommandError

from apps.nodes.models import Node


def _encode_token(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")


def _load_node_command():
    app_name = get_commands()["node"]
    return load_command_class(app_name, "node")


def test_node_register_requires_https_urls():
    token = _encode_token(
        {
            "register": "http://example.com/nodes/register/",
            "info": "https://example.com/nodes/info/",
            "username": "user",
            "password": "pass",
        }
    )
    command = _load_node_command()

    with pytest.raises(CommandError, match="Host registration URL must use https"):
        command.handle(action="register", token=token)


def test_node_register_rejects_private_host_in_token():
    token = _encode_token(
        {
            "register": "https://127.0.0.1/nodes/register/",
            "info": "https://example.com/nodes/info/",
            "username": "user",
            "password": "pass",
        }
    )
    command = _load_node_command()

    with pytest.raises(CommandError, match="Host registration URL host must not resolve"):
        command.handle(action="register", token=token)


@pytest.mark.django_db
def test_discovered_same_host_instance_forces_sibling_relation(monkeypatch):
    command = _load_node_command()
    local_node = Node.objects.create(
        hostname="local",
        mac_address="aa:bb:cc:dd:ee:01",
        host_instance_id="machine-1",
        current_relation=Node.Relation.SELF,
        port=8888,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: local_node))
    info = {
        "hostname": "local-alt",
        "mac_address": "aa:bb:cc:dd:ee:02",
        "host_instance_id": "machine-1",
        "uuid": str(local_node.uuid),
        "port": 8890,
    }

    payload = command._build_discovered_peer_payload(info)

    assert payload["current_relation"] == Node.Relation.SIBLING


@pytest.mark.django_db
def test_discovered_different_host_instance_keeps_peer_relation(monkeypatch):
    command = _load_node_command()
    local_node = Node.objects.create(
        hostname="local",
        mac_address="aa:bb:cc:dd:ee:03",
        host_instance_id="machine-1",
        current_relation=Node.Relation.SELF,
        port=8888,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: local_node))
    info = {
        "hostname": "remote",
        "mac_address": "aa:bb:cc:dd:ee:04",
        "host_instance_id": "machine-2",
        "uuid": "7bbf70fd-99e7-4f30-b1fe-c453ce15e2ad",
        "port": 8890,
    }

    payload = command._build_discovered_peer_payload(info)

    assert payload["current_relation"] == "Peer"
