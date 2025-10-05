import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django


django.setup()

from django.test import Client, TestCase
from django.urls import reverse
from urllib.parse import quote
from unittest.mock import patch

from nodes.models import Node, NodeRole


class DashboardAccessTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _set_role(self, role_name: str):
        role, _ = NodeRole.objects.get_or_create(name=role_name)
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": "localhost",
                "address": "127.0.0.1",
                "role": role,
            },
        )
        return role

    def test_satellite_dashboard_allows_anonymous(self):
        self._set_role("Satellite")
        response = self.client.get(reverse("ocpp-dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_terminal_dashboard_requires_login(self):
        self._set_role("Terminal")
        response = self.client.get(reverse("ocpp-dashboard"))
        login_url = reverse("pages:login")
        expected_next = quote(reverse("ocpp-dashboard"))
        self.assertRedirects(response, f"{login_url}?next={expected_next}")


class RfidAccessTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _set_role(self, role_name: str):
        role, _ = NodeRole.objects.get_or_create(name=role_name)
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": "localhost",
                "address": "127.0.0.1",
                "role": role,
            },
        )
        return role

    @patch("ocpp.rfid.views.scan_sources", return_value={"rfid": None})
    def test_control_reader_allows_anonymous(self, mock_scan):
        self._set_role("Control")
        response = self.client.get(reverse("rfid-reader"))
        self.assertEqual(response.status_code, 200)
        scan_response = self.client.get(reverse("rfid-scan-next"))
        self.assertEqual(scan_response.status_code, 200)
        mock_scan.assert_called()

    def test_terminal_reader_requires_login(self):
        self._set_role("Terminal")
        response = self.client.get(reverse("rfid-reader"))
        login_url = reverse("pages:login")
        expected_next = quote(reverse("rfid-reader"))
        self.assertRedirects(response, f"{login_url}?next={expected_next}")
        scan_response = self.client.get(reverse("rfid-scan-next"))
        expected_next_scan = quote(reverse("rfid-scan-next"))
        self.assertRedirects(
            scan_response, f"{login_url}?next={expected_next_scan}", fetch_redirect_response=False
        )
