import os
from unittest.mock import patch, MagicMock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rfid.reader import read_rfid


class ScanNextViewTests(SimpleTestCase):
    @patch("config.middleware.get_site")
    @patch("rfid.views.scan_sources", return_value={"rfid": "ABCD1234", "label_id": 1, "created": False})
    def test_scan_next_success(self, mock_scan, mock_site):
        resp = self.client.get(reverse("rfid-scan-next"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"rfid": "ABCD1234", "label_id": 1, "created": False})

    @patch("config.middleware.get_site")
    @patch("rfid.views.scan_sources", return_value={"error": "boom"})
    def test_scan_next_error(self, mock_scan, mock_site):
        resp = self.client.get(reverse("rfid-scan-next"))
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": "boom"})


class ReaderNotificationTests(TestCase):
    def _mock_reader(self):
        class MockReader:
            MI_OK = 1
            PICC_REQIDL = 0

            def MFRC522_Request(self, _):
                return (self.MI_OK, None)

            def MFRC522_Anticoll(self):
                return (self.MI_OK, [0xAB, 0xCD, 0x12, 0x34, 0x56])

        return MockReader()

    @patch("nodes.notifications.notify")
    @patch("accounts.models.RFID.objects.get_or_create")
    def test_notify_on_allowed_tag(self, mock_get, mock_notify):
        reference = MagicMock(value="https://example.com")
        tag = MagicMock(
            label_id=1,
            pk=1,
            allowed=True,
            color="black",
            released=False,
            reference=reference,
        )
        mock_get.return_value = (tag, False)

        result = read_rfid(mfrc=self._mock_reader(), cleanup=False)
        self.assertEqual(result["label_id"], 1)
        self.assertEqual(result["reference"], "https://example.com")
        mock_notify.assert_called_once_with(
            "RFID 1 OK INT", f"{result['rfid']} BLACK"
        )

    @patch("nodes.notifications.notify")
    @patch("accounts.models.RFID.objects.get_or_create")
    def test_notify_on_disallowed_tag(self, mock_get, mock_notify):
        tag = MagicMock(
            label_id=2,
            pk=2,
            allowed=False,
            color="black",
            released=False,
            reference=None,
        )
        mock_get.return_value = (tag, False)

        result = read_rfid(mfrc=self._mock_reader(), cleanup=False)
        mock_notify.assert_called_once_with(
            "RFID 2 BAD INT", f"{result['rfid']} BLACK"
        )


class RestartViewTests(SimpleTestCase):
    @patch("config.middleware.get_site")
    @patch("rfid.views.restart_sources", return_value={"status": "restarted"})
    def test_restart_endpoint(self, mock_restart, mock_site):
        resp = self.client.post(reverse("rfid-scan-restart"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "restarted"})
        mock_restart.assert_called_once()


class ScanTestViewTests(SimpleTestCase):
    @patch("config.middleware.get_site")
    @patch("rfid.views.test_sources", return_value={"irq_pin": 7})
    def test_scan_test_success(self, mock_test, mock_site):
        resp = self.client.get(reverse("rfid-scan-test"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"irq_pin": 7})

    @patch("config.middleware.get_site")
    @patch(
        "rfid.views.test_sources",
        return_value={"error": "no scanner detected"},
    )
    def test_scan_test_error(self, mock_test, mock_site):
        resp = self.client.get(reverse("rfid-scan-test"))
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": "no scanner detected"})

