import os
from unittest.mock import patch, MagicMock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rfid.reader import read_rfid
from rfid.scanner import scan_sources
from accounts.models import RFIDSource


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
    def setUp(self):
        RFIDSource.objects.create(name="local", endpoint="scanner")
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
        tag = MagicMock(label_id=1, pk=1, allowed=True, color="black", released=False, source=None)
        mock_get.return_value = (tag, False)

        result = read_rfid(mfrc=self._mock_reader(), cleanup=False)
        self.assertEqual(result["label_id"], 1)
        self.assertTrue(result.get("source"))
        mock_notify.assert_called_once_with(
            "RFID 1 OK PRIV", f"{result['rfid']} BLACK"
        )

    @patch("nodes.notifications.notify")
    @patch("accounts.models.RFID.objects.get_or_create")
    def test_notify_on_disallowed_tag(self, mock_get, mock_notify):
        tag = MagicMock(label_id=2, pk=2, allowed=False, color="black", released=False, source=None)
        mock_get.return_value = (tag, False)

        result = read_rfid(mfrc=self._mock_reader(), cleanup=False)
        mock_notify.assert_called_once_with(
            "RFID 2 Not OK PRIV", f"{result['rfid']} BLACK"
        )
        self.assertTrue(result.get("source"))


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
    @patch("rfid.irq_wiring_check.GPIO")
    @patch("rfid.irq_wiring_check.IRQ_PIN", new=7)
    @patch("rfid.irq_wiring_check._setup_hardware", return_value=True)
    def test_scan_test_success(self, mock_setup, mock_gpio, mock_site):
        resp = self.client.get(reverse("rfid-scan-test"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"irq_pin": 7})

    @patch("config.middleware.get_site")
    @patch("rfid.irq_wiring_check._setup_hardware", return_value=False)
    def test_scan_test_error(self, mock_setup, mock_site):
        resp = self.client.get(reverse("rfid-scan-test"))
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": "no scanner detected"})

class ScannerSelectionTests(TestCase):
    def setUp(self):
        RFIDSource.objects.create(name="local", endpoint="scanner", default_order=0)
        RFIDSource.objects.create(
            name="remote", endpoint="scanner", proxy_url="http://example.com", default_order=1
        )

    @patch("rfid.scanner.requests.get")
    @patch("rfid.scanner.get_next_tag", return_value=None)
    def test_falls_back_to_remote(self, mock_get_next, mock_get):
        mock_get.return_value = MagicMock(json=lambda: {"rfid": "REMOTE", "label_id": 42}, status_code=200)
        result = scan_sources()
        self.assertEqual(result, {"rfid": "REMOTE", "label_id": 42})
        mock_get_next.assert_called_once()
        mock_get.assert_called_once()

    @patch("rfid.scanner.requests.get")
    @patch("rfid.scanner.get_next_tag", return_value={"rfid": "LOCAL", "label_id": 1})
    def test_local_preferred(self, mock_get_next, mock_get):
        result = scan_sources()
        self.assertEqual(result["rfid"], "LOCAL")
        mock_get_next.assert_called_once()
        mock_get.assert_not_called()


class ScanNextFallbackTests(TestCase):
    def setUp(self):
        RFIDSource.objects.create(name="local", endpoint="scanner", default_order=0)
        RFIDSource.objects.create(
            name="remote", endpoint="scanner", proxy_url="http://example.com", default_order=1
        )

    @patch("config.middleware.get_site")
    @patch("rfid.scanner.requests.get")
    @patch("rfid.scanner.get_next_tag", return_value=None)
    def test_view_uses_remote_when_local_missing(self, mock_get_next, mock_get, mock_site):
        mock_get.return_value = MagicMock(json=lambda: {"rfid": "REMOTE", "label_id": 7}, status_code=200)
        resp = self.client.get(reverse("rfid-scan-next"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"rfid": "REMOTE", "label_id": 7})
