from io import BytesIO
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from pathlib import Path
from PIL import Image


class GameTests(TestCase):
    def setUp(self):
        fixture = Path(settings.BASE_DIR, "game", "fixtures", "game.json")
        call_command("loaddata", str(fixture))
        self.client = Client()

    def _mock_response(self):
        img = Image.new("RGB", (10, 10), "white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()

        class R:
            content = data

        return R()

    def test_game_list_displays_fixture(self):
        resp = self.client.get(reverse("game:game-list"))
        self.assertContains(resp, "Simple Demo Game")

    def test_game_detail_view(self):
        resp = self.client.get(reverse("game:game-detail", args=["simple"]))
        self.assertContains(resp, "Simple Demo Game")
        self.assertContains(resp, reverse("game:material-detail", args=["start"]))

    def test_game_material_view(self):
        resp = self.client.get(reverse("game:material-detail", args=["start"]))
        self.assertContains(resp, "Choose one")
        self.assertContains(resp, reverse("game:region-follow", args=[1]))
        self.assertContains(resp, reverse("game:region-follow", args=[2]))

    def test_auto_entry_material_created(self):
        with patch("game.models.requests.get", return_value=self._mock_response()):
            self.client.get(reverse("game:game-detail", args=["endless-return"]))
        from .models import GamePortal

        portal = GamePortal.objects.get(slug="endless-return")
        self.assertIsNotNone(portal.entry_material)

    def test_follow_region_creates_material(self):
        with patch("game.models.requests.get", return_value=self._mock_response()):
            resp = self.client.get(reverse("game:region-follow", args=[2]))
        self.assertEqual(resp.status_code, 302)
        from .models import MaterialRegion

        region = MaterialRegion.objects.get(pk=2)
        self.assertIsNotNone(region.target)

