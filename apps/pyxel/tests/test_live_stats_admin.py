"""Regression tests for the admin Pyxel live-stats launcher."""

from __future__ import annotations

from django.urls import reverse
import pytest

from apps.pyxel import admin_views, live_stats


pytestmark = [pytest.mark.django_db]


def test_is_local_request_matches_local_interface(monkeypatch, rf):
    """Local requests should be accepted when REMOTE_ADDR matches a local IP."""

    request = rf.get("/admin/", REMOTE_ADDR="192.168.1.20")
    monkeypatch.setattr(live_stats, "local_ip_addresses", lambda include_loopback=True: {"192.168.1.20"})

    assert live_stats.is_local_request(request) is True


def test_is_local_request_rejects_non_local_ip(monkeypatch, rf):
    """Non-local requests should never expose local-only Pyxel controls."""

    request = rf.get("/admin/", REMOTE_ADDR="203.0.113.7")
    monkeypatch.setattr(live_stats, "local_ip_addresses", lambda include_loopback=True: {"127.0.0.1"})

    assert live_stats.is_local_request(request) is False


def test_is_local_request_ignores_forwarded_for_header(monkeypatch, rf):
    """Forwarded headers must not grant local access for remote socket peers."""

    request = rf.get(
        "/admin/",
        REMOTE_ADDR="203.0.113.7",
        HTTP_X_FORWARDED_FOR="127.0.0.1",
    )
    monkeypatch.setattr(live_stats, "local_ip_addresses", lambda include_loopback=True: {"127.0.0.1"})

    assert live_stats.is_local_request(request) is False


def test_open_live_stats_view_blocks_remote_request(client, django_user_model, monkeypatch):
    """The launcher endpoint returns forbidden for remote client addresses."""

    admin_user = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    client.force_login(admin_user)
    monkeypatch.setattr(live_stats, "local_ip_addresses", lambda include_loopback=True: {"127.0.0.1"})

    response = client.post(reverse("admin-pyxel-live-stats"), REMOTE_ADDR="10.0.0.8")

    assert response.status_code == 403


