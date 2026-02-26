"""Regression tests for the admin Pyxel live-stats launcher."""

from __future__ import annotations

from django.urls import reverse
import pytest

from apps.pyxel import admin_views, live_stats


pytestmark = [pytest.mark.django_db, pytest.mark.regression]


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


def test_admin_index_shows_pyxel_button_for_local_request(client, django_user_model, monkeypatch):
    """Dashboard renders the Pyxel button only for local admin sessions."""

    admin_user = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    client.force_login(admin_user)
    monkeypatch.setattr(live_stats, "local_ip_addresses", lambda include_loopback=True: {"127.0.0.1"})

    response = client.get(reverse("admin:index"), REMOTE_ADDR="127.0.0.1")

    assert response.status_code == 200
    assert b">Pyxel</button>" in response.content
    assert b"Pyxel Live Stats" not in response.content


def test_admin_index_hides_pyxel_button_for_remote_request(client, django_user_model, monkeypatch):
    """Dashboard hides the launcher button for non-local client IP addresses."""

    admin_user = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    client.force_login(admin_user)
    monkeypatch.setattr(live_stats, "local_ip_addresses", lambda include_loopback=True: {"127.0.0.1"})

    response = client.get(reverse("admin:index"), REMOTE_ADDR="10.12.0.5")

    assert response.status_code == 200
    assert b">Pyxel</button>" not in response.content


def test_open_live_stats_view_launches_subprocess_for_local_request(
    client,
    django_user_model,
    monkeypatch,
):
    """Posting the admin action starts the detached live-stats subprocess."""

    admin_user = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    client.force_login(admin_user)
    monkeypatch.setattr(live_stats, "local_ip_addresses", lambda include_loopback=True: {"127.0.0.1"})

    called = {"value": False}

    def _fake_launch():
        called["value"] = True
        return object()

    monkeypatch.setattr(admin_views, "launch_live_stats_subprocess", _fake_launch)

    response = client.post(reverse("admin-pyxel-live-stats"), REMOTE_ADDR="127.0.0.1")

    assert response.status_code == 302
    assert called["value"] is True


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
