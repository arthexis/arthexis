from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment, teardown_test_environment
from django.urls import reverse

from nodes.models import Node, NodeRole
from pages.models import ViewHistory


ROLE_FIXTURE_NAMES = {
    "Watchtower": [
        "default__application_pages.json",
        "watchtower__application_ocpp.json",
        "watchtower__module_ocpp.json",
        "watchtower__module_readme.json",
        "watchtower__landing_ocpp_dashboard.json",
        "watchtower__landing_ocpp_cp_simulator.json",
        "watchtower__landing_ocpp_rfid.json",
        "watchtower__landing_readme.json",
    ],
    "Control": [
        "default__application_pages.json",
        "control__application_ocpp.json",
        "control__module_ocpp.json",
        "control__module_readme.json",
        "control__landing_ocpp_dashboard.json",
        "control__landing_ocpp_cp_simulator.json",
        "control__landing_ocpp_rfid.json",
        "control__landing_readme.json",
    ],
}


@pytest.fixture(scope="module", autouse=True)
def enable_django_test_environment():
    setup_test_environment()
    yield
    teardown_test_environment()


def _load_role_fixtures(role_name: str) -> None:
    fixture_dir = Path(settings.BASE_DIR, "pages", "fixtures")
    fixture_paths = [fixture_dir / name for name in ROLE_FIXTURE_NAMES[role_name]]
    call_command("loaddata", *map(str, fixture_paths))


def _prepare_role_environment(role_name: str) -> None:
    role, _ = NodeRole.objects.get_or_create(name=role_name)
    Node.objects.update_or_create(
        mac_address=Node.get_current_mac(),
        defaults={
            "hostname": "localhost",
            "address": "127.0.0.1",
            "role": role,
        },
    )
    site_defaults = {
        "Watchtower": {"domain": "arthexis.com", "name": "Arthexis"},
        "Control": {"domain": "testserver", "name": ""},
    }
    Site.objects.update_or_create(id=1, defaults=site_defaults[role_name])


@pytest.mark.django_db
@pytest.mark.parametrize(
    "role_name, requires_login",
    [("Watchtower", False), ("Control", True)],
)
def test_navigation_landings_return_success(role_name, requires_login):
    ViewHistory.objects.all().delete()
    navigation_client = Client()
    _prepare_role_environment(role_name)
    _load_role_fixtures(role_name)

    if requires_login:
        user = get_user_model().objects.create_user(
            username=f"{role_name.lower()}-user", password="password"
        )
        navigation_client.force_login(user)

    response = navigation_client.get(reverse("pages:index"))
    assert response.status_code == 200
    nav_modules = list(response.context["nav_modules"])
    assert nav_modules, "Expected navigation modules to be available"

    visited_paths = set()
    for module in nav_modules:
        for landing in module.enabled_landings:
            landing_response = navigation_client.get(landing.path, follow=True)
            assert (
                landing_response.status_code < 400
            ), f"{landing.path} returned {landing_response.status_code}"
            visited_paths.add(landing.path)

    assert visited_paths, "Expected to crawl at least one landing path"
    assert not ViewHistory.objects.filter(status_code__gte=400).exists()
