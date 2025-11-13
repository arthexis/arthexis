from django.contrib.sites.models import Site
from django.test import TestCase, override_settings


class SuiteNotFoundPageTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": "Arthexis"}
        )

    @override_settings(DEBUG=False)
    def test_custom_404_template_is_used(self):
        response = self.client.get("/definitely-not-here/")

        self.assertContains(response, "Return to the main page", status_code=404)
        self.assertContains(response, "data-redirect-countdown", status_code=404)
        self.assertContains(response, "window.location.assign", status_code=404)

    @override_settings(DEBUG=True)
    def test_debug_mode_keeps_technical_404(self):
        response = self.client.get("/debug-missing-page/")

        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "Return to the main page", status_code=404)
        self.assertNotIn("window.location.assign", response.content.decode("utf-8"))
