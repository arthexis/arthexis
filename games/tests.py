from django.conf import settings
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from pathlib import Path


class GameListTests(TestCase):
    def setUp(self):
        fixture = Path(settings.BASE_DIR, "games", "fixtures", "games.json")
        call_command("loaddata", str(fixture))
        self.client = Client()

    def test_games_list_displays_fixture(self):
        resp = self.client.get(reverse("games:game-list"))
        self.assertContains(resp, "Demo Ren&#x27;Py Game")

    def test_game_detail_view(self):
        resp = self.client.get(reverse("games:game-detail", args=["demo"]))
        self.assertContains(resp, "Demo Ren&#x27;Py Game")
