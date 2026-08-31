import base64
import io
import json
import os
import subprocess
import sys

import pytest
from django.core.management import call_command, get_commands, load_command_class
from django.core.management.base import CommandError
from django.http import JsonResponse

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


def test_node_register_curl_defaults_to_http_localhost():
    stdout = io.StringIO()

    call_command(
        "node",
        "register_curl",
        "https://example.com",
        "--token",
        "fixedtoken",
        stdout=stdout,
    )

    script = stdout.getvalue()
    assert 'LOCAL_INFO="http://localhost:8888/nodes/info/"' in script
    assert 'LOCAL_REGISTER="http://localhost:8888/nodes/register/"' in script
    assert "build_registration_payload() {" in script
    assert 'data = json.loads(os.environ["INFO_JSON"])' in script
    assert 'downstream_info="$(curl -fsSL "${LOCAL_INFO}?token=${TOKEN}")"' in script
    assert (
        'downstream_payload="$(build_registration_payload "${downstream_info}" '
        '"Downstream" "${NODE_RESERVED_CLAIM_TOKEN:-}")"'
    ) in script
    assert 'upstream_info="$(curl -fsSL "${UPSTREAM_INFO}?token=${TOKEN}")"' in script
    assert (
        'upstream_payload="$(build_registration_payload "${upstream_info}" "Upstream")"'
        in script
    )
    assert 'RESERVATION_CLAIM_TOKEN="${reservation_claim_token}"' in script
    assert 'payload["reservation_claim_token"] = reservation_claim_token' in script
    assert "json.load(sys.stdin)" not in script
    assert '| \\\n    TOKEN="${TOKEN}" RELATION=' not in script


def test_node_register_curl_payload_builder_rejects_non_object_info_json():
    stdout = io.StringIO()

    call_command(
        "node",
        "register_curl",
        "https://example.com",
        "--token",
        "fixedtoken",
        stdout=stdout,
    )

    script = stdout.getvalue()
    heredoc_start = "python - <<'PY'\n"
    python_start = script.index(heredoc_start) + len(heredoc_start)
    python_end = script.index("\nPY\n", python_start)
    payload_builder = script[python_start:python_end]
    env = {
        **os.environ,
        "INFO_JSON": "[]",
        "TOKEN": "fixedtoken",
        "RELATION": "Downstream",
        "RESERVATION_CLAIM_TOKEN": "",
    }

    result = subprocess.run(
        [sys.executable, "-c", payload_builder],
        capture_output=True,
        env=env,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "/nodes/info/ returned a non-object JSON payload" in result.stderr


def test_downstream_registration_payload_includes_local_reservation_claim_token(
    monkeypatch,
):
    monkeypatch.setenv("NODE_RESERVED_CLAIM_TOKEN", "claim-token")
    payload = _load_node_command()._build_registration_payload(
        {
            "hostname": "gway-005",
            "mac_address": "aa:bb:cc:dd:ef:05",
            "public_key": "public-key",
        },
        "Downstream",
    )

    assert payload["reservation_claim_token"] == "claim-token"


def test_upstream_registration_payload_omits_local_reservation_claim_token(monkeypatch):
    monkeypatch.setenv("NODE_RESERVED_CLAIM_TOKEN", "claim-token")
    payload = _load_node_command()._build_registration_payload(
        {
            "hostname": "watchtower",
            "mac_address": "aa:bb:cc:dd:ef:06",
            "public_key": "public-key",
        },
        "Upstream",
    )

    assert "reservation_claim_token" not in payload


@pytest.mark.django_db
def test_node_info_json_omits_local_reservation_claim_token(monkeypatch):
    local_mac = "aa:bb:cc:dd:ef:05"
    stdout = io.StringIO()
    Node._local_cache.clear()
    monkeypatch.setenv("NODE_RESERVED_CLAIM_TOKEN", "claim-token")
    monkeypatch.setattr(Node, "get_current_mac", staticmethod(lambda: local_mac))
    Node.objects.create(
        hostname="gway-005",
        mac_address=local_mac,
        current_relation=Node.Relation.SELF,
    )

    call_command("node", "info_json", stdout=stdout)
    info = json.loads(stdout.getvalue())

    assert "reservation_claim_token" not in info


def test_node_command_load_local_info_rejects_non_dictionary_payload(monkeypatch):
    command = _load_node_command()
    command_module = sys.modules[command.__module__]
    monkeypatch.setattr(
        command_module,
        "node_info",
        lambda request: JsonResponse(["invalid"], safe=False),
    )

    with pytest.raises(CommandError, match="Local node information payload is invalid"):
        command._load_local_info()


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
