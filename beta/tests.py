from django.conf import settings
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from pathlib import Path


class BetaTests(TestCase):
    def setUp(self):
        fixture = Path(settings.BASE_DIR, "beta", "fixtures", "beta.json")
        call_command("loaddata", str(fixture))
        self.client = Client()

    def test_portal_list_displays_fixture(self):
        resp = self.client.get(reverse("beta:portal-list"))
        self.assertContains(resp, "Eternal Return")

    def test_portal_detail_view(self):
        resp = self.client.get(reverse("beta:portal-detail", args=["baseline"]))
        self.assertContains(resp, "Eternal Return")
        self.assertContains(resp, reverse("beta:material-detail", args=["start"]))

    def test_game_material_view(self):
        resp = self.client.get(reverse("beta:material-detail", args=["start"]))
        self.assertContains(resp, "Choose one")
        self.assertContains(resp, "Go Left")
        self.assertContains(resp, "Go Right")

