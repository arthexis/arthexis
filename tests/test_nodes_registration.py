import json
import logging
from unittest.mock import Mock

import pytest
from django.urls import reverse

from apps.nodes.models import Node


@pytest.mark.django_db
def test_node_info_registers_missing_local(client, monkeypatch):
    node = Node.objects.create(
        hostname="local",
        address="127.0.0.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local-endpoint",
    )

    register_spy = Mock(return_value=(node, True))

    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: None))
    monkeypatch.setattr(Node, "register_current", classmethod(lambda cls: register_spy()))

    response = client.get(reverse("node-info"))

    register_spy.assert_called_once_with()
    assert response.status_code == 200
    payload = response.json()
    assert payload["mac_address"] == node.mac_address
    assert payload["network_hostname"] == node.network_hostname
    assert payload["features"] == []


@pytest.mark.django_db
def test_node_changelist_excludes_register_local_tool(admin_client):
    response = admin_client.get(reverse("admin:nodes_node_changelist"))

    assert response.status_code == 200
    assert "Register local host" not in response.content.decode()


@pytest.mark.django_db
def test_register_visitor_view_uses_clean_visitor_base(admin_client, monkeypatch):
    node = Node.objects.create(
        hostname="local",
        address="127.0.0.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local-endpoint",
    )

    call_count: dict[str, int] = {"count": 0}

    def fake_register_current(cls):
        call_count["count"] += 1
        return node, False

    monkeypatch.setattr(Node, "register_current", classmethod(fake_register_current))

    response = admin_client.get(
        reverse("admin:nodes_node_register_visitor"),
        {"visitor": "visitor.example.com:9999/extra/path"},
    )

    assert response.status_code == 200
    assert call_count["count"] == 1

    context = response.context[-1]
    assert context["token"]
    assert context["info_url"] == reverse("node-info")
    assert context["register_url"] == reverse("register-node")
    assert context["telemetry_url"] == reverse("register-telemetry")
    assert context["visitor_info_url"] == "http://visitor.example.com:9999/nodes/info/"
    assert (
        context["visitor_register_url"]
        == "http://visitor.example.com:9999/nodes/register/"
    )


@pytest.mark.django_db
def test_register_visitor_view_detects_remote_addr(admin_client, monkeypatch):
    node = Node.objects.create(
        hostname="local",
        address="127.0.0.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local-endpoint",
    )

    monkeypatch.setattr(Node, "register_current", classmethod(lambda cls: (node, False)))

    response = admin_client.get(
        reverse("admin:nodes_node_register_visitor"),
        REMOTE_ADDR="192.0.2.10",
    )

    assert response.status_code == 200
    context = response.context[-1]
    assert context["visitor_error"] is None
    assert context["visitor_info_url"] == "http://192.0.2.10:8888/nodes/info/"
    assert context["visitor_register_url"] == "http://192.0.2.10:8888/nodes/register/"
    assert context["telemetry_url"] == reverse("register-telemetry")


@pytest.mark.django_db
def test_register_visitor_view_prefers_forwarded_for(admin_client, monkeypatch):
    node = Node.objects.create(
        hostname="local",
        address="127.0.0.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local-endpoint",
    )

    monkeypatch.setattr(Node, "register_current", classmethod(lambda cls: (node, False)))

    response = admin_client.get(
        reverse("admin:nodes_node_register_visitor"),
        REMOTE_ADDR="198.51.100.5",
        HTTP_X_FORWARDED_FOR="203.0.113.1, 203.0.113.2",
    )

    assert response.status_code == 200
    context = response.context[-1]
    assert context["visitor_error"] is None
    assert context["visitor_info_url"] == "http://203.0.113.1:8888/nodes/info/"
    assert context["visitor_register_url"] == "http://203.0.113.1:8888/nodes/register/"
    assert context["telemetry_url"] == reverse("register-telemetry")


@pytest.mark.django_db
def test_register_visitor_view_defaults_loopback_port(admin_client, monkeypatch):
    node = Node.objects.create(
        hostname="local",
        address="127.0.0.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="local-endpoint",
    )

    monkeypatch.setattr(Node, "register_current", classmethod(lambda cls: (node, False)))

    response = admin_client.get(
        reverse("admin:nodes_node_register_visitor"),
        REMOTE_ADDR="127.0.0.1",
    )

    assert response.status_code == 200
    context = response.context[-1]
    assert context["visitor_error"] is None
    assert context["visitor_info_url"] == "http://127.0.0.1:8000/nodes/info/"
    assert context["visitor_register_url"] == "http://127.0.0.1:8000/nodes/register/"
    assert context["telemetry_url"] == reverse("register-telemetry")


@pytest.mark.django_db
def test_register_visitor_telemetry_logs(client, caplog):
    url = reverse("register-telemetry")
    payload = {
        "stage": "integration-test",
        "message": "failed to fetch",
        "target": "http://example.com/nodes/info/",
        "token": "abc123",
        "extra": {"networkIssue": True},
    }

    with caplog.at_level(logging.INFO, logger="register_visitor_node"):
        response = client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_USER_AGENT="pytest-agent/1.0",
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "telemetry stage=integration-test" in caplog.text


@pytest.mark.django_db
def test_register_visitor_telemetry_adds_route_ip(client, caplog, monkeypatch):
    url = reverse("register-telemetry")
    payload = {
        "stage": "integration-test",
        "message": "failed to fetch",
        "target": "https://example.com/nodes/info/",
        "token": "abc123",
    }

    monkeypatch.setattr(
        "apps.nodes.views._get_route_address", lambda host, port: "10.0.0.5"
    )

    with caplog.at_level(logging.INFO, logger="register_visitor_node"):
        response = client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_USER_AGENT="pytest-agent/1.0",
        )

    assert response.status_code == 200
    assert "host_ip=10.0.0.5" in caplog.text
    assert '"target_host": "example.com"' in caplog.text
