import json
import logging

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import RequestFactory

from apps.nodes.models import Node, NodeRole
from apps.nodes.views import node_info, register_node


@pytest.fixture
def admin_user(db):
    User = get_user_model()
    return User.objects.create_superuser(
        username="admin", email="admin@example.com", password="password"
    )


def _build_request(factory, payload):
    request = factory.post(
        "/nodes/register/",
        data=json.dumps(payload),
        content_type="application/json",
    )
    return request


@pytest.mark.django_db
def test_register_node_logs_attempt_and_success(admin_user, caplog):
    NodeRole.objects.get_or_create(name="Terminal")
    payload = {
        "hostname": "visitor-host",
        "mac_address": "aa:bb:cc:dd:ee:ff",
        "address": "192.0.2.10",
        "port": 8888,
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    caplog.set_level(logging.INFO, logger="apps.nodes.views")
    response = register_node(request)

    assert response.status_code == 200
    messages = [record.getMessage() for record in caplog.records]
    assert any("Node registration attempt" in message for message in messages)
    assert any("Node registration succeeded" in message for message in messages)


@pytest.mark.django_db
def test_register_node_logs_validation_failure(admin_user, caplog):
    factory = RequestFactory()
    request = _build_request(
        factory,
        {
            "hostname": "missing-mac",
            "address": "198.51.100.10",
        },
    )
    request.user = admin_user
    request._cached_user = admin_user

    caplog.set_level(logging.INFO, logger="apps.nodes.views")
    response = register_node(request)

    assert response.status_code == 400
    messages = [record.getMessage() for record in caplog.records]
    assert any("Node registration attempt" in message for message in messages)
    assert any("Node registration failed" in message for message in messages)


@pytest.mark.django_db
def test_register_node_sets_cors_headers_without_origin(admin_user):
    payload = {
        "hostname": "visitor-host",
        "mac_address": "aa:bb:cc:dd:ee:11",
        "address": "192.0.2.20",
        "port": 8888,
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    assert response.status_code == 200
    assert response["Access-Control-Allow-Origin"] == "*"
    assert response["Access-Control-Allow-Headers"] == "Content-Type"
    assert response["Access-Control-Allow-Methods"] == "POST, OPTIONS"


@pytest.mark.django_db
def test_register_node_allows_authenticated_user_with_invalid_signature(admin_user):
    payload = {
        "hostname": "visitor-host",
        "mac_address": "aa:bb:cc:dd:ee:22",
        "address": "192.0.2.30",
        "port": 8888,
        "public_key": "invalid-key",
        "token": "signed-token",
        "signature": "bad-signature",
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    assert response.status_code == 200
    node = Node.objects.get(mac_address=payload["mac_address"])
    assert node.hostname == payload["hostname"]


@pytest.mark.django_db
def test_register_node_links_base_site_when_domain_matches(admin_user):
    site = Site.objects.create(domain="linked.example.com", name="Linked")
    payload = {
        "hostname": "visitor-host",
        "mac_address": "aa:bb:cc:dd:ee:33",
        "address": "192.0.2.40",
        "port": 8888,
        "base_site_domain": site.domain,
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    assert response.status_code == 200
    node = Node.objects.get(mac_address=payload["mac_address"])
    assert node.base_site_id == site.id


@pytest.mark.django_db
def test_register_node_updates_base_site_for_existing_node(admin_user):
    site = Site.objects.create(domain="update.example.com", name="Update")
    node = Node.objects.create(
        hostname="existing",
        mac_address="aa:bb:cc:dd:ee:44",
        address="198.51.100.40",
        port=8888,
        public_endpoint="existing-endpoint",
    )
    payload = {
        "hostname": node.hostname,
        "mac_address": node.mac_address,
        "address": node.address,
        "port": node.port,
        "base_site_domain": site.domain,
    }

    factory = RequestFactory()
    request = _build_request(factory, payload)
    request.user = admin_user
    request._cached_user = admin_user

    response = register_node(request)

    assert response.status_code == 200
    node.refresh_from_db()
    assert node.base_site_id == site.id


@pytest.mark.django_db
def test_register_current_logs_to_local_logger(settings, caplog):
    settings.LOG_DIR = settings.BASE_DIR / "logs"
    NodeRole.objects.get_or_create(name="Terminal")

    caplog.set_level(logging.INFO, logger="register_local_node")

    node, created = Node.register_current(notify_peers=False)

    assert node is not None
    assert caplog.records
    messages = [record.getMessage() for record in caplog.records]
    assert any("Local node registration started" in message for message in messages)
    assert any(
        "Local node registration created" in message
        or "Local node registration updated" in message
        or "Local node registration refreshed" in message
        for message in messages
    )


@pytest.mark.django_db
def test_register_current_uses_managed_site_domain(settings, caplog):
    caplog.set_level(logging.INFO, logger="register_local_node")
    Node.objects.all().delete()
    Node._local_cache.clear()
    NodeRole.objects.get_or_create(name="Terminal")

    site = Site.objects.get_current()
    site.domain = "arthexis.com"
    site.name = "Arthexis"
    site.managed = True
    site.require_https = True
    site.save()

    node, created = Node.register_current(notify_peers=False)

    assert created
    assert node.hostname == "arthexis.com"
    assert node.network_hostname == "arthexis.com"
    assert node.address == "arthexis.com"
    assert node.base_site_id == site.id
    assert node.port == 443


@pytest.mark.django_db
def test_node_info_prefers_base_site_domain(monkeypatch):
    site = Site.objects.create(domain="base.example.test", name="Base Example")
    node = Node.objects.create(
        hostname="original.local",
        mac_address="01:23:45:67:89:ab",
        port=8888,
        public_endpoint="base-example",
        base_site=site,
    )

    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    factory = RequestFactory()
    request = factory.get("/nodes/info/")

    response = node_info(request)
    data = json.loads(response.content.decode())

    assert data["hostname"] == "base.example.test"
    assert data["address"] == "base.example.test"
    assert data["contact_hosts"][0] == "base.example.test"
    assert data["base_site_domain"] == site.domain
