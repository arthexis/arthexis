from importlib import import_module

from django.conf import settings
from django.test import SimpleTestCase

SELECTED_APPS = (
    "apps.base",
    "apps.cards",
    "apps.celery",
    "apps.energy",
    "apps.events",
    "apps.nodes",
    "apps.ocpp",
    "apps.sigils",
)


class SelectedAppsTests(SimpleTestCase):
    def test_all_selected_apps_have_static_registry_entries(self) -> None:
        self.assertTrue(set(SELECTED_APPS).issubset(settings.INSTALLED_APPS))

    def test_all_selected_apps_publish_manifest_metadata(self) -> None:
        for app_name in SELECTED_APPS:
            manifest = import_module(f"{app_name}.manifest")
            self.assertEqual(manifest.NAME, app_name.rsplit(".", 1)[-1])
            self.assertTrue(manifest.DESCRIPTION)

    def test_ocpp_http_boundary_is_available(self) -> None:
        response = self.client.get("/ocpp/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], "2.0")
