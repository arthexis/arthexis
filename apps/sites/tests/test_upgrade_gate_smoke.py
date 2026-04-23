from __future__ import annotations

import pytest
from django.urls import reverse

from gate_markers import gate


pytestmark = [pytest.mark.django_db, gate.upgrade]


def test_public_login_route_renders(client):
    response = client.get(reverse("pages:login"))

    assert response.status_code == 200
    assert 'name="username"' in response.content.decode()


def test_admin_login_route_renders(client):
    response = client.get(reverse("admin:login"))

    assert response.status_code == 200
    assert 'name="username"' in response.content.decode()
