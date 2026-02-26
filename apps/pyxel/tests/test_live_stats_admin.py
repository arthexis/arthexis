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
    assert b'<input type="submit" class="button" value="Pyxel">' in response.content
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
    assert b'<input type="submit" class="button" value="Pyxel">' not in response.content


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


def test_launch_live_stats_subprocess_raises_when_process_exits(monkeypatch):
    """Launcher should raise a clear error when the viewport process exits immediately."""

    class _ExitedProcess:
        def poll(self):
            return 1

        def communicate(self):
            return ("", "display init failed")

    monkeypatch.setattr(live_stats.subprocess, "Popen", lambda *args, **kwargs: _ExitedProcess())

    with pytest.raises(live_stats.PyxelLiveStatsLaunchError, match="display init failed"):
        live_stats.launch_live_stats_subprocess()


def test_launch_live_stats_subprocess_returns_running_process(monkeypatch):
    """Launcher should keep the success path when the viewport process remains running."""

    class _RunningProcess:
        def __init__(self):
            self.calls = 0

        def poll(self):
            self.calls += 1
            return None

    process = _RunningProcess()
    monotonic_values = iter([0.0, 0.2, 0.4, 0.6, 0.8, 1.2])

    monkeypatch.setattr(live_stats.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(live_stats.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(live_stats.time, "sleep", lambda _duration: None)

    returned_process = live_stats.launch_live_stats_subprocess()

    assert returned_process is process
