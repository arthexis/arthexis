import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.contrib.sessions.middleware import SessionMiddleware
from django.urls import reverse

from accounts.admin import RFIDAdmin
from accounts.models import RFID


class RFIDAdminActionTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = RFIDAdmin(RFID, self.site)
        self.factory = RequestFactory()
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="rfidadmin", email="rfid@example.com", password="password"
        )

    def _request_with_messages(self):
        request = self.factory.post("/admin/accounts/rfid/")
        request.user = self.user
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def test_swap_color_action_registered(self):
        request = self.factory.get("/admin/accounts/rfid/")
        request.user = self.user
        actions = self.admin.get_actions(request)
        self.assertIn("swap_color", actions)

    def test_swap_color_action_swaps_colors(self):
        black = RFID.objects.create(rfid="00112233", color=RFID.BLACK)
        white = RFID.objects.create(rfid="44556677", color=RFID.WHITE)
        request = self._request_with_messages()
        queryset = RFID.objects.filter(pk__in=[black.pk, white.pk])
        self.admin.swap_color(request, queryset)
        black.refresh_from_db()
        white.refresh_from_db()
        self.assertEqual(black.color, RFID.WHITE)
        self.assertEqual(white.color, RFID.BLACK)

