import pytest

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import Client, RequestFactory
from django.urls import reverse

import core.tasks
from core.admin import WorldSimulatorAdmin
from core.models import WorldSimulator, WorldSimulatorClientSession
from core.tasks import launch_world_simulator, world_simulator_watchdog


@pytest.mark.django_db
def test_world_simulator_generates_slug():
    simulator = WorldSimulator.objects.create(name="Test World")
    assert simulator.slug == "test-world"


@pytest.mark.django_db
def test_launch_world_simulator_task(monkeypatch):
    simulator = WorldSimulator.objects.create(name="Task Runner")

    started = []
    scheduled = []

    def fake_start(self, ensure_watchdog=False):
        started.append(ensure_watchdog)
        return True

    def fake_schedule(self, countdown=1):
        scheduled.append(countdown)

    monkeypatch.setattr(WorldSimulator, "start", fake_start, raising=False)
    monkeypatch.setattr(WorldSimulator, "schedule_watchdog", fake_schedule, raising=False)

    result = launch_world_simulator(simulator.pk)

    assert result is True
    assert started == [False]
    assert scheduled == [1]


@pytest.mark.django_db
def test_world_simulator_watchdog_task(monkeypatch):
    simulator = WorldSimulator.objects.create(name="Watchdog Runner")

    monkeypatch.setattr(WorldSimulator, "run_watchdog", lambda self: True, raising=False)
    calls = []
    monkeypatch.setattr(WorldSimulator, "schedule_watchdog", lambda self: calls.append(True), raising=False)

    result = world_simulator_watchdog(simulator.pk)

    assert result is True
    assert calls == [True]


@pytest.mark.django_db
def test_world_simulator_admin_start_action(monkeypatch):
    simulator = WorldSimulator.objects.create(name="Admin World")
    factory = RequestFactory()
    request = factory.post("/")

    user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pass")
    request.user = user
    request.session = {}
    messages = FallbackStorage(request)
    setattr(request, "_messages", messages)

    admin_site = django_admin.site
    admin_instance = WorldSimulatorAdmin(WorldSimulator, admin_site)

    calls = []
    monkeypatch.setattr(
        core.tasks.launch_world_simulator,
        "delay",
        lambda simulator_id: calls.append(simulator_id),
    )

    admin_instance.start_world(request, simulator)

    assert calls == [simulator.pk]


@pytest.mark.django_db
def test_world_simulator_client_view(monkeypatch):
    user = get_user_model().objects.create_user(
        username="viewer", password="pass", is_staff=True
    )
    client = Client()
    client.force_login(user)

    simulator = WorldSimulator.objects.create(name="Viewer World")

    session = WorldSimulatorClientSession(session_key="abc123", account_username="viewer")

    monkeypatch.setattr(
        WorldSimulator,
        "prepare_client_session",
        lambda self, u: session,
        raising=False,
    )
    monkeypatch.setattr(
        core.tasks.world_simulator_watchdog,
        "apply_async",
        lambda *args, **kwargs: None,
    )

    response = client.get(reverse("core:world-simulator-client", args=[simulator.pk]))

    assert response.status_code == 200
    assert simulator.client_url in response.content.decode()
    assert "automatically authenticated" in response.content.decode()
    assert response.cookies["sessionid"].value == "abc123"
