from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse


class ScanNextViewTests(TestCase):
    @patch("rfid.views.read_rfid", return_value={"rfid": "ABCD1234", "label_id": 1, "created": False})
    def test_scan_next_success(self, mock_read):
        resp = self.client.get(reverse("rfid-scan-next"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"rfid": "ABCD1234", "label_id": 1, "created": False})

    @patch("rfid.views.read_rfid", return_value={"error": "boom"})
    def test_scan_next_error(self, mock_read):
        resp = self.client.get(reverse("rfid-scan-next"))
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": "boom"})
