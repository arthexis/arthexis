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


def test_admin_index_omits_pyxel_button(client, django_user_model, monkeypatch):
    """Dashboard no longer renders the old top-row Pyxel button."""

    admin_user = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    client.force_login(admin_user)
    monkeypatch.setattr(live_stats, "local_ip_addresses", lambda include_loopback=True: {"127.0.0.1"})

    response = client.get(reverse("admin:index"), REMOTE_ADDR="127.0.0.1")

    assert response.status_code == 200
    assert b"value=\"Pyxel\"" not in response.content


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


def test_pyxel_changelist_exposes_open_viewport_action(client, django_user_model, monkeypatch):
    """Pyxel changelist should render the Open Viewport action in object tools."""

    from apps.pyxel.models import PyxelViewport

    admin_user = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    client.force_login(admin_user)
    monkeypatch.setattr(live_stats, "local_ip_addresses", lambda include_loopback=True: {"127.0.0.1"})
    PyxelViewport.objects.update(is_default=False)
    PyxelViewport.objects.create(
        slug="test-vp",
        name="Test VP",
        skin="virtual",
        columns=10,
        rows=10,
        pyxel_script="def draw():\n    pass",
    )

    response = client.get(reverse("admin:pyxel_pyxelviewport_changelist"), REMOTE_ADDR="127.0.0.1")

    assert response.status_code == 200
    assert reverse("admin-pyxel-open-viewport") in response.content.decode()




def test_admin_dashboard_model_row_includes_open_viewport_link(
    client, django_user_model, monkeypatch
):
    """Regression: dashboard row Open Viewport should trigger the launch endpoint."""

    admin_user = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    client.force_login(admin_user)
    monkeypatch.setattr(live_stats, "local_ip_addresses", lambda include_loopback=True: {"127.0.0.1"})

    response = client.get(reverse("admin:index"), REMOTE_ADDR="127.0.0.1")

    assert response.status_code == 200
    content = response.content.decode()
    launch_url = reverse("admin-pyxel-open-viewport")
    assert launch_url in content
    assert f'<form method="post" action="{launch_url}"' in content
    assert "Open Viewport" in content

def test_open_viewport_view_uses_default_when_multiple(client, django_user_model, monkeypatch):
    """Open Viewport launches the default viewport when more than one exists."""

    from apps.pyxel import admin_views as pyxel_admin_views
    from apps.pyxel.models import PyxelViewport

    admin_user = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    client.force_login(admin_user)
    monkeypatch.setattr(live_stats, "local_ip_addresses", lambda include_loopback=True: {"127.0.0.1"})

    PyxelViewport.objects.update(is_default=False)
    PyxelViewport.objects.create(
        slug="secondary",
        name="Secondary",
        skin="virtual",
        columns=10,
        rows=10,
        pyxel_script="def draw():\n    pass",
        is_default=False,
    )
    default_viewport = PyxelViewport.objects.create(
        slug="default",
        name="Default",
        skin="virtual",
        columns=10,
        rows=10,
        pyxel_script="def draw():\n    pass",
        is_default=True,
    )

    called = {"slug": None}

    def _fake_launch(*, viewport_slug=None):
        called["slug"] = viewport_slug
        return object()

    monkeypatch.setattr(pyxel_admin_views, "launch_viewport_subprocess", _fake_launch)

    response = client.post(reverse("admin-pyxel-open-viewport"), REMOTE_ADDR="127.0.0.1")

    assert response.status_code == 302
    assert called["slug"] == default_viewport.slug


def test_open_viewport_view_by_pk_launches_requested_viewport(client, django_user_model, monkeypatch):
    """Object-level action should launch the specific viewport represented by the change form."""

    from apps.pyxel import admin_views as pyxel_admin_views
    from apps.pyxel.models import PyxelViewport

    admin_user = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    client.force_login(admin_user)
    monkeypatch.setattr(live_stats, "local_ip_addresses", lambda include_loopback=True: {"127.0.0.1"})

    PyxelViewport.objects.update(is_default=False)
    viewport = PyxelViewport.objects.create(
        slug="single",
        name="Single",
        skin="virtual",
        columns=10,
        rows=10,
        pyxel_script="def draw():\n    pass",
    )

    called = {"slug": None}

    def _fake_launch(*, viewport_slug=None):
        called["slug"] = viewport_slug
        return object()

    monkeypatch.setattr(pyxel_admin_views, "launch_viewport_subprocess", _fake_launch)

    response = client.post(
        reverse("admin-pyxel-open-viewport-specific", args=[viewport.pk]),
        REMOTE_ADDR="127.0.0.1",
    )

    assert response.status_code == 302
    assert called["slug"] == viewport.slug


def test_soft_deleted_seed_default_does_not_block_new_default():
    """Soft-deleting a seeded default should allow promoting another default viewport."""

    from apps.pyxel.models import PyxelViewport

    PyxelViewport.all_objects.update(is_default=False)

    seeded_default = PyxelViewport.objects.create(
        slug="seeded-default",
        name="Seeded Default",
        skin="virtual",
        columns=8,
        rows=8,
        pyxel_script="def draw():\n    pass",
        is_default=True,
        is_seed_data=True,
    )

    seeded_default.delete()

    assert PyxelViewport.all_objects.get(pk=seeded_default.pk).is_default is False

    replacement = PyxelViewport.objects.create(
        slug="replacement-default",
        name="Replacement Default",
        skin="virtual",
        columns=8,
        rows=8,
        pyxel_script="def draw():\n    pass",
        is_default=True,
    )

    assert replacement.is_default is True


def test_soft_deleted_non_seed_default_does_not_block_new_default():
    """Deleting a non-seeded default should allow promoting a replacement default."""

    from apps.pyxel.models import PyxelViewport

    PyxelViewport.all_objects.update(is_default=False)

    default_viewport = PyxelViewport.objects.create(
        slug="non-seeded-default",
        name="Non-seeded Default",
        skin="virtual",
        columns=8,
        rows=8,
        pyxel_script="def draw():\n    pass",
        is_default=True,
    )

    deleted_pk = default_viewport.pk
    default_viewport.delete()

    assert not PyxelViewport.all_objects.filter(pk=deleted_pk).exists()

    replacement = PyxelViewport.objects.create(
        slug="non-seeded-replacement-default",
        name="Non-seeded Replacement Default",
        skin="virtual",
        columns=8,
        rows=8,
        pyxel_script="def draw():\n    pass",
        is_default=True,
    )

    assert replacement.is_default is True
