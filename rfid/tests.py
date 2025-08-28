import os
from unittest.mock import patch, MagicMock, call

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from core.models import RFID
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

    @patch("rfid.reader.notify_async")
    @patch("core.models.RFID.objects.get_or_create")
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
        self.assertEqual(mock_notify.call_count, 1)
        mock_notify.assert_has_calls(
            [call("RFID 1 OK", f"{result['rfid']} BLACK")]
        )

    @patch("rfid.reader.notify_async")
    @patch("core.models.RFID.objects.get_or_create")
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
        self.assertEqual(mock_notify.call_count, 1)
        mock_notify.assert_has_calls(
            [call("RFID 2 BAD", f"{result['rfid']} BLACK")]
        )


class RFIDLastSeenTests(TestCase):
    def _mock_reader(self):
        class MockReader:
            MI_OK = 1
            PICC_REQIDL = 0

            def MFRC522_Request(self, _):
                return (self.MI_OK, None)

            def MFRC522_Anticoll(self):
                return (self.MI_OK, [0xAB, 0xCD, 0x12, 0x34])

        return MockReader()

    @patch("rfid.reader.notify_async")
    def test_last_seen_updated_on_read(self, _mock_notify):
        tag = RFID.objects.create(rfid="ABCD1234")
        read_rfid(mfrc=self._mock_reader(), cleanup=False)
        tag.refresh_from_db()
        self.assertIsNotNone(tag.last_seen_on)


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


class ScannerTemplateTests(TestCase):
    def test_configure_link_for_staff(self):
        User = get_user_model()
        staff = User.objects.create_user("staff", password="pwd", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(reverse("rfid-reader"))
        self.assertContains(resp, 'id="rfid-configure"')

    def test_no_link_for_anonymous(self):
        resp = self.client.get(reverse("rfid-reader"))
        self.assertNotContains(resp, 'id="rfid-configure"')


class ReaderPollingTests(SimpleTestCase):
    def _mock_reader_no_tag(self):
        class MockReader:
            MI_OK = 1
            PICC_REQIDL = 0

            def MFRC522_Request(self, _):
                return (0, None)

        return MockReader()

    @patch("rfid.reader.time.sleep")
    def test_poll_interval_used(self, mock_sleep):
        read_rfid(
            mfrc=self._mock_reader_no_tag(),
            cleanup=False,
            timeout=0.002,
            poll_interval=0.001,
        )
        mock_sleep.assert_called_with(0.001)

    @patch("rfid.reader.time.sleep")
    def test_use_irq_skips_sleep(self, mock_sleep):
        read_rfid(
            mfrc=self._mock_reader_no_tag(),
            cleanup=False,
            timeout=0.002,
            use_irq=True,
        )
        mock_sleep.assert_not_called()


