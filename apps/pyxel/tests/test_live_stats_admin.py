"""Regression tests for the admin Pyxel live-stats launcher."""

from __future__ import annotations

from django.urls import reverse
import pytest

from apps.pyxel import live_stats


pytestmark = [pytest.mark.django_db]


def test_open_live_stats_view_blocks_remote_request(client, django_user_model, monkeypatch):
    """The launcher endpoint returns forbidden for remote client addresses."""

    admin_user = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    client.force_login(admin_user)
    monkeypatch.setattr(
        live_stats,
        "local_ip_addresses",
        lambda include_loopback=True: {"127.0.0.1"},
    )

    response = client.post(reverse("admin-pyxel-live-stats"), REMOTE_ADDR="10.0.0.8")

    assert response.status_code == 403


def test_open_live_stats_view_allows_local_request(client, django_user_model, monkeypatch):
    """The launcher endpoint allows requests from local client addresses."""

    admin_user = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    client.force_login(admin_user)
    monkeypatch.setattr(
        live_stats,
        "local_ip_addresses",
        lambda include_loopback=True: {"127.0.0.1"},
    )
    monkeypatch.setattr(live_stats, "launch_live_stats_subprocess", lambda: None)

    response = client.post(reverse("admin-pyxel-live-stats"), REMOTE_ADDR="127.0.0.1")

    assert response.status_code == 302
