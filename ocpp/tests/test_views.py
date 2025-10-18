import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django


django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.shortcuts import resolve_url
from django.test import Client, TestCase
from django.urls import reverse
from urllib.parse import quote
from unittest.mock import patch

from nodes.models import Node, NodeRole
from ocpp.models import Charger


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


class ChargerAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.charger = Charger.objects.create(charger_id="C1", public_display=True)
        owner = get_user_model().objects.create_user(
            username="owner",
            email="owner@example.com",
            password="test-password",
        )
        self.charger.owner_users.add(owner)

    def test_restricted_charger_redirects_to_login(self):
        path = reverse("charger-page", args=[self.charger.charger_id])
        response = self.client.get(path)
        login_url = resolve_url(settings.LOGIN_URL)
        expected_next = quote(path)
        self.assertRedirects(
            response,
            f"{login_url}?next={expected_next}",
            fetch_redirect_response=False,
        )

    def test_charger_status_redirects_to_login(self):
        path = reverse("charger-status", args=[self.charger.charger_id])
        response = self.client.get(path)
        login_url = resolve_url(settings.LOGIN_URL)
        expected_next = quote(path)
        self.assertRedirects(
            response,
            f"{login_url}?next={expected_next}",
            fetch_redirect_response=False,
        )
