import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django


django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.shortcuts import resolve_url
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils.translation import gettext
from urllib.parse import quote
from unittest.mock import patch

from nodes.models import Node, NodeRole
from ocpp.models import Charger
from ocpp.views import charger_log_page


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


class ChargerLogViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="viewer",
            email="viewer@example.com",
            password="test-password",
        )
        self.charger = Charger.objects.create(
            charger_id="CP-01",
            public_display=True,
        )
        self.charger.owner_users.add(self.user)
        self.client.force_login(self.user)

    def _request(self, params: dict | None = None):
        path = reverse("charger-log", args=[self.charger.charger_id])
        request = self.factory.get(path, data=params or {})
        request.user = self.user
        request.session = self.client.session
        return request

    def _render_context(self, entries, params: dict | None = None):
        request = self._request(params)
        with patch("ocpp.views.store.get_logs", return_value=entries), patch(
            "ocpp.views.render"
        ) as mock_render:
            mock_render.return_value = HttpResponse()
            charger_log_page(request, self.charger.charger_id)
        context = mock_render.call_args[0][2]
        return context

    def test_log_view_uses_expected_limit_options(self):
        entries = [f"line {i}" for i in range(1, 6)]
        context = self._render_context(entries)
        self.assertEqual(
            context["log_limit_options"],
            [
                {"value": "20", "label": "20"},
                {"value": "40", "label": "40"},
                {"value": "100", "label": "100"},
                {"value": "all", "label": gettext("All")},
            ],
        )
        self.assertEqual(context["log_limit_choice"], "20")
        self.assertEqual(context["log_limit_label"], "20")

    def test_log_view_applies_numeric_limit(self):
        entries = [f"entry {i}" for i in range(1, 101)]
        context = self._render_context(entries, params={"limit": "40"})
        rendered_entries = context["log"]
        self.assertEqual(len(rendered_entries), 40)
        self.assertEqual(rendered_entries[0], "entry 61")
        self.assertEqual(rendered_entries[-1], "entry 100")

    def test_log_view_all_limit_returns_every_entry(self):
        entries = ["first", "second", "third"]
        context = self._render_context(entries, params={"limit": "all"})
        rendered_entries = context["log"]
        self.assertEqual(rendered_entries, entries)

    def test_log_view_download_streams_full_log(self):
        entries = ["download one", "download two"]
        request = self._request(params={"download": "1"})
        with patch("ocpp.views.store.get_logs", return_value=entries):
            response = charger_log_page(request, self.charger.charger_id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))
        content = response.content.decode("utf-8")
        self.assertEqual(content, "download one\ndownload two\n")
