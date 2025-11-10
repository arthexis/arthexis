from __future__ import annotations

from django.contrib.sites.models import Site
from django.http import Http404
from django.test import TestCase, override_settings
from django.urls import path


def raise_debug_404(request):
    raise Http404("missing resource for debug")


def raise_debug_500(request):
    raise ValueError("simulated debug failure")


urlpatterns = [
    path("debug-404/", raise_debug_404),
    path("debug-500/", raise_debug_500),
]


@override_settings(ROOT_URLCONF=__name__)
class DebugErrorPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Site.objects.update_or_create(
            pk=1,
            defaults={"domain": "testserver", "name": "Test Server"},
        )

    @override_settings(DEBUG=True)
    def test_debug_404_shows_detailed_message(self):
        from django.conf import settings

        self.assertTrue(settings.DEBUG)
        response = self.client.get("/debug-404/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)
        self.assertContains(response, "missing resource for debug", status_code=404)

    @override_settings(DEBUG=True)
    def test_debug_500_shows_exception_details(self):
        from django.conf import settings

        self.assertTrue(settings.DEBUG)
        response = self.client.get("/debug-500/")
        self.assertEqual(response.status_code, 500)
        self.assertContains(response, "ValueError", status_code=500)
        self.assertContains(response, "simulated debug failure", status_code=500)

    @override_settings(DEBUG=False)
    def test_non_debug_uses_standard_responses(self):
        response = self.client.get("/debug-404/")
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "Resolver", status_code=404)
