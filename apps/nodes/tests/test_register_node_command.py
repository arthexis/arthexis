import pytest
from django.core.management import get_commands, load_command_class

from apps.nodes.models import Node

def _load_node_command():
    app_name = get_commands()["node"]
    return load_command_class(app_name, "node")




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




@pytest.mark.django_db
def test_discover_skips_local_node_without_remote_uuid(monkeypatch):
    command = _load_node_command()
    local_node = Node.objects.create(
        hostname="local",
        mac_address="aa:bb:cc:dd:ee:06",
        host_instance_id="machine-1",
        current_relation=Node.Relation.SELF,
        port=8888,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: local_node))
    monkeypatch.setattr(command, "_parse_ports", lambda _: [8888])
    monkeypatch.setattr(command, "_parse_interfaces", lambda _: ["eth0"])
    monkeypatch.setattr(command, "_collect_local_ip_addresses", lambda: set())
    monkeypatch.setattr(command, "_iter_interface_hosts", lambda *_args: iter(["198.51.100.60"]))
    monkeypatch.setattr(command, "_iter_known_interface_hosts", lambda *_args: iter(()))
    monkeypatch.setattr(
        command,
        "_probe_node_info",
        lambda *_args, **_kwargs: {
            "hostname": "local-self",
            "mac_address": "aa:bb:cc:dd:ee:06",
            "port": 8888,
        },
    )
    registered_payloads = []
    monkeypatch.setattr(
        command,
        "_register_host_locally",
        lambda payload: registered_payloads.append(payload),
    )

    command._handle_discover(
        ports="8888",
        timeout=0.1,
        max_hosts=2,
        interfaces="eth0",
    )

    assert not registered_payloads
