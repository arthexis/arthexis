from unittest.mock import patch

import os
import django
from django.test import SimpleTestCase
from django.urls import reverse

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


class ScanNextViewTests(SimpleTestCase):
    @patch("utils.sites.get_site")
    @patch("rfid.views.get_next_tag", return_value={"rfid": "ABCD1234", "label_id": 1, "created": False})
    def test_scan_next_success(self, mock_get, mock_site):
        resp = self.client.get(reverse("rfid-scan-next"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"rfid": "ABCD1234", "label_id": 1, "created": False})

    @patch("utils.sites.get_site")
    @patch("rfid.views.get_next_tag", return_value={"error": "boom"})
    def test_scan_next_error(self, mock_get, mock_site):
        resp = self.client.get(reverse("rfid-scan-next"))
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": "boom"})
