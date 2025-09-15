from __future__ import annotations

import datetime
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from core import wifi
from core.models import WiFiLead


class WiFiLeadLoginTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="login-user", email="user@example.com", password="password"
        )
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.lease_file = Path(self.tmpdir.name) / "leases"

    def _request(self, ip: str = "10.42.0.20"):
        request = self.factory.get("/admin/login/")
        request.META["REMOTE_ADDR"] = ip
        request.META["HTTP_USER_AGENT"] = "TestAgent/1.0"
        request.META["HTTP_REFERER"] = "http://example.com/login/"
        return request

    def test_handle_user_login_creates_lead(self) -> None:
        expiry = int((timezone.now() + datetime.timedelta(hours=1)).timestamp())
        self.lease_file.write_text(
            f"{expiry} aa:bb:cc:dd:ee:ff 10.42.0.20 test *\n",
            encoding="utf-8",
        )
        with mock.patch("core.wifi.LEASE_PATHS", [self.lease_file]):
            with mock.patch("core.wifi.allow_client_internet") as allow_internet, mock.patch(
                "core.wifi.allow_staff_ports"
            ) as allow_staff:
                wifi.handle_user_login(self._request(), self.user)
        lead = WiFiLead.objects.get()
        self.assertEqual(lead.user, self.user)
        self.assertEqual(lead.ip_address, "10.42.0.20")
        self.assertEqual(lead.mac_address, "AA:BB:CC:DD:EE:FF")
        self.assertIsNotNone(lead.last_seen)
        self.assertIsNotNone(lead.lease_expires)
        allow_internet.assert_called_once_with("aa:bb:cc:dd:ee:ff")
        allow_staff.assert_not_called()

    def test_handle_user_login_staff_allows_ports(self) -> None:
        self.user.is_staff = True
        self.user.save()
        with mock.patch("core.wifi.LEASE_PATHS", [self.lease_file]):
            with mock.patch(
                "core.wifi._mac_from_neigh", return_value="aa:bb:cc:dd:ee:ff"
            ) as neigh:
                with mock.patch("core.wifi.allow_client_internet") as allow_internet:
                    with mock.patch("core.wifi.allow_staff_ports") as allow_staff:
                        wifi.handle_user_login(self._request(), self.user)
        lead = WiFiLead.objects.get()
        self.assertEqual(lead.mac_address, "AA:BB:CC:DD:EE:FF")
        self.assertIsNone(lead.lease_expires)
        neigh.assert_called_once()
        allow_internet.assert_called_once_with("aa:bb:cc:dd:ee:ff")
        allow_staff.assert_called_once_with("aa:bb:cc:dd:ee:ff")

    def test_handle_user_login_ignored_outside_network(self) -> None:
        with mock.patch("core.wifi.LEASE_PATHS", [self.lease_file]):
            with mock.patch("core.wifi.allow_client_internet") as allow_internet, mock.patch(
                "core.wifi.allow_staff_ports"
            ) as allow_staff:
                wifi.handle_user_login(self._request("192.168.0.4"), self.user)
        self.assertFalse(WiFiLead.objects.exists())
        allow_internet.assert_not_called()
        allow_staff.assert_not_called()
