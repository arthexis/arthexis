import base64
import io
import json
import sys

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


def test_node_register_accepts_multiline_token_input():
    command = _load_node_command()
    token = command._encode_token(
        {
            "register": "https://example.com/nodes/register/",
            "info": "https://example.com/nodes/info/",
            "username": "cli-user",
            "password": "cli-pass",
        }
    )

    payload = command._decode_token_from_input(f"Version: test\n{token}\n")

    assert payload == {
        "register": "https://example.com/nodes/register/",
        "info": "https://example.com/nodes/info/",
        "username": "cli-user",
        "password": "cli-pass",
    }


def test_node_token_generates_register_consumable_payload():
    command = _load_node_command()

    token = command._encode_token(
        {
            "register": "https://example.com/nodes/register/",
            "info": "https://example.com/nodes/info/",
            "username": "cli-user",
            "password": "cli-pass",
        }
    )

    decoded = command._decode_token(token)

    assert decoded == {
        "register": "https://example.com/nodes/register/",
        "info": "https://example.com/nodes/info/",
        "username": "cli-user",
        "password": "cli-pass",
    }


def test_node_token_rejects_private_hosts():
    command = _load_node_command()

    with pytest.raises(CommandError, match="Host info URL host must not resolve"):
        command.handle(
            action="token",
            host="https://127.0.0.1",
            username="cli-user",
            password="cli-pass",
            json=False,
        )


def test_node_token_accepts_password_from_env(monkeypatch):
    command = _load_node_command()
    monkeypatch.setenv("NODE_PASSWORD", "env-pass")
    command.stdout = io.StringIO()

    result = command.handle(
        action="token",
        host="https://example.com",
        username="cli-user",
        password="",
        password_env="NODE_PASSWORD",
        password_stdin=False,
        json=False,
    )

    token = command.stdout.getvalue().strip()
    decoded = command._decode_token(token)
    assert decoded["password"] == "env-pass"
    assert result is None


def test_node_token_accepts_password_from_stdin(monkeypatch):
    command = _load_node_command()
    monkeypatch.setattr(sys, "stdin", io.StringIO("stdin-pass\n"))
    command.stdout = io.StringIO()

    result = command.handle(
        action="token",
        host="https://example.com",
        username="cli-user",
        password="",
        password_env="",
        password_stdin=True,
        json=False,
    )

    token = command.stdout.getvalue().strip()
    decoded = command._decode_token(token)
    assert decoded["password"] == "stdin-pass"
    assert result is None


def test_node_token_requires_single_password_source():
    command = _load_node_command()

    with pytest.raises(
        CommandError,
        match="Provide exactly one of --password, --password-env, or --password-stdin.",
    ):
        command.handle(
            action="token",
            host="https://example.com",
            username="cli-user",
            password="inline-pass",
            password_env="NODE_PASSWORD",
            password_stdin=False,
            json=False,
        )


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
