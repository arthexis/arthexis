from unittest.mock import patch, MagicMock

import os
import django
from django.test import SimpleTestCase
from django.urls import reverse
from rfid.reader import read_rfid

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


class ReaderNotificationTests(SimpleTestCase):
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
        tag = MagicMock(label_id=1, allowed=True, color="black", released=False)
        mock_get.return_value = (tag, False)

        result = read_rfid(mfrc=self._mock_reader(), cleanup=False)
        self.assertEqual(result["label_id"], 1)
        mock_notify.assert_called_once_with("Label 1 Ok", result["rfid"])

    @patch("nodes.notifications.notify")
    @patch("accounts.models.RFID.objects.get_or_create")
    def test_notify_on_disallowed_tag(self, mock_get, mock_notify):
        tag = MagicMock(label_id=2, allowed=False, color="black", released=False)
        mock_get.return_value = (tag, False)

        result = read_rfid(mfrc=self._mock_reader(), cleanup=False)
        mock_notify.assert_called_once_with("Label 2 Not Ok", result["rfid"])
