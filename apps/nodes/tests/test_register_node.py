import json
import logging

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.nodes.models import NodeRole
from apps.nodes.views import register_node


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
