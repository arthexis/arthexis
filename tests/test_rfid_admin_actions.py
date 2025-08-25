import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase
from unittest.mock import MagicMock

from accounts.admin import RFIDAdmin
from accounts.models import RFID


class RFIDAdminActionTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = RFIDAdmin(RFID, self.site)
        self.factory = RequestFactory()

        class DummyUser:
            is_active = True

            def has_perm(self, _perm):
                return True

        self.user = DummyUser()
        self.admin.message_user = MagicMock()

    def _request_with_messages(self):
        request = self.factory.post("/admin/accounts/rfid/")
        request.user = self.user
        return request

    def test_swap_color_action_registered(self):
        request = self.factory.get("/admin/accounts/rfid/")
        request.user = self.user
        actions = self.admin.get_actions(request)
        self.assertIn("swap_color", actions)

    def test_swap_color_action_swaps_colors(self):
        black = MagicMock(color=RFID.BLACK)
        white = MagicMock(color=RFID.WHITE)
        request = self._request_with_messages()
        queryset = [black, white]
        self.admin.swap_color(request, queryset)
        self.assertEqual(black.color, RFID.WHITE)
        self.assertEqual(white.color, RFID.BLACK)
        black.save.assert_called_once()
        white.save.assert_called_once()
