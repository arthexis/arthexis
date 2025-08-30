import os
from unittest.mock import patch, MagicMock, call

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site

from pages.models import Application, Module
from nodes.models import Node, NodeRole

from core.models import RFID
from ocpp.rfid.reader import read_rfid, enable_deep_read


class ScanNextViewTests(SimpleTestCase):
    @patch("config.middleware.Node.get_local", return_value=None)
    @patch("config.middleware.get_site")
    @patch("ocpp.rfid.views.scan_sources", return_value={"rfid": "ABCD1234", "label_id": 1, "created": False})
    def test_scan_next_success(self, mock_scan, mock_site, mock_node):
        resp = self.client.get(reverse("rfid-scan-next"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"rfid": "ABCD1234", "label_id": 1, "created": False})

    @patch("config.middleware.Node.get_local", return_value=None)
    @patch("config.middleware.get_site")
    @patch("ocpp.rfid.views.scan_sources", return_value={"error": "boom"})
    def test_scan_next_error(self, mock_scan, mock_site, mock_node):
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

    @patch("ocpp.rfid.reader.notify_async")
    @patch("core.models.RFID.objects.get_or_create")
    def test_notify_on_allowed_tag(self, mock_get, mock_notify):
        reference = MagicMock(value="https://example.com")
        tag = MagicMock(
            label_id=1,
            pk=1,
            allowed=True,
            color="B",
            released=False,
            reference=reference,
        )
        mock_get.return_value = (tag, False)

        result = read_rfid(mfrc=self._mock_reader(), cleanup=False)
        self.assertEqual(result["label_id"], 1)
        self.assertEqual(result["reference"], "https://example.com")
        self.assertEqual(mock_notify.call_count, 1)
        mock_notify.assert_has_calls(
            [call("RFID 1 OK", f"{result['rfid']} B")]
        )

    @patch("ocpp.rfid.reader.notify_async")
    @patch("core.models.RFID.objects.get_or_create")
    def test_notify_on_disallowed_tag(self, mock_get, mock_notify):
        tag = MagicMock(
            label_id=2,
            pk=2,
            allowed=False,
            color="B",
            released=False,
            reference=None,
        )
        mock_get.return_value = (tag, False)

        result = read_rfid(mfrc=self._mock_reader(), cleanup=False)
        self.assertEqual(mock_notify.call_count, 1)
        mock_notify.assert_has_calls(
            [call("RFID 2 BAD", f"{result['rfid']} B")]
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

    @patch("ocpp.rfid.reader.notify_async")
    def test_last_seen_updated_on_read(self, _mock_notify):
        tag = RFID.objects.create(rfid="ABCD1234")
        read_rfid(mfrc=self._mock_reader(), cleanup=False)
        tag.refresh_from_db()
        self.assertIsNotNone(tag.last_seen_on)


class RestartViewTests(SimpleTestCase):
    @patch("config.middleware.Node.get_local", return_value=None)
    @patch("config.middleware.get_site")
    @patch("ocpp.rfid.views.restart_sources", return_value={"status": "restarted"})
    def test_restart_endpoint(self, mock_restart, mock_site, mock_node):
        resp = self.client.post(reverse("rfid-scan-restart"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "restarted"})
        mock_restart.assert_called_once()


class ScanTestViewTests(SimpleTestCase):
    @patch("config.middleware.Node.get_local", return_value=None)
    @patch("config.middleware.get_site")
    @patch("ocpp.rfid.views.test_sources", return_value={"irq_pin": 7})
    def test_scan_test_success(self, mock_test, mock_site, mock_node):
        resp = self.client.get(reverse("rfid-scan-test"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"irq_pin": 7})

    @patch("config.middleware.Node.get_local", return_value=None)
    @patch("config.middleware.get_site")
    @patch(
        "ocpp.rfid.views.test_sources",
        return_value={"error": "no scanner detected"},
    )
    def test_scan_test_error(self, mock_test, mock_site, mock_node):
        resp = self.client.get(reverse("rfid-scan-test"))
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": "no scanner detected"})


class RFIDLandingTests(TestCase):
    def test_scanner_view_registered_as_landing(self):
        role, _ = NodeRole.objects.get_or_create(name="Terminal")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={"hostname": "localhost", "address": "127.0.0.1", "role": role},
        )
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": ""}
        )
        app = Application.objects.create(name="Ocpp")
        module = Module.objects.create(node_role=role, application=app, path="/ocpp/")
        module.create_landings()
        self.assertTrue(
            module.landings.filter(path="/ocpp/rfid/").exists()
        )

class ScannerTemplateTests(TestCase):
    def setUp(self):
        self.url = reverse("rfid-reader")

    def test_configure_link_for_staff(self):
        User = get_user_model()
        staff = User.objects.create_user("staff", password="pwd", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'id="rfid-configure"')

    def test_no_link_for_anonymous(self):
        resp = self.client.get(self.url)
        self.assertNotContains(resp, 'id="rfid-configure"')

    def test_advanced_fields_for_staff(self):
        User = get_user_model()
        staff = User.objects.create_user("staff2", password="pwd", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'id="rfid-rfid"')
        self.assertContains(resp, 'id="rfid-released"')
        self.assertContains(resp, 'id="rfid-reference"')

    def test_basic_fields_for_public(self):
        resp = self.client.get(self.url)
        self.assertNotContains(resp, 'id="rfid-rfid"')
        self.assertNotContains(resp, 'id="rfid-released"')
        self.assertNotContains(resp, 'id="rfid-reference"')

    def test_deep_read_button_for_staff(self):
        User = get_user_model()
        staff = User.objects.create_user("staff3", password="pwd", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'id="rfid-deep-read"')

    def test_no_deep_read_button_for_public(self):
        resp = self.client.get(self.url)
        self.assertNotContains(resp, 'id="rfid-deep-read"')


class ReaderPollingTests(SimpleTestCase):
    def _mock_reader_no_tag(self):
        class MockReader:
            MI_OK = 1
            PICC_REQIDL = 0

            def MFRC522_Request(self, _):
                return (0, None)

        return MockReader()

    @patch("ocpp.rfid.reader.time.sleep")
    def test_poll_interval_used(self, mock_sleep):
        read_rfid(
            mfrc=self._mock_reader_no_tag(),
            cleanup=False,
            timeout=0.002,
            poll_interval=0.001,
        )
        mock_sleep.assert_called_with(0.001)

    @patch("ocpp.rfid.reader.time.sleep")
    def test_use_irq_skips_sleep(self, mock_sleep):
        read_rfid(
            mfrc=self._mock_reader_no_tag(),
            cleanup=False,
            timeout=0.002,
            use_irq=True,
        )
        mock_sleep.assert_not_called()


class DeepReadViewTests(TestCase):
    @patch("config.middleware.Node.get_local", return_value=None)
    @patch("config.middleware.get_site")
    @patch("ocpp.rfid.views.enable_deep_read_mode", return_value={"status": "deep", "timeout": 60})
    def test_enable_deep_read(self, mock_enable, mock_site, mock_node):
        User = get_user_model()
        staff = User.objects.create_user("staff4", password="pwd", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.post(reverse("rfid-scan-deep"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "deep", "timeout": 60})
        mock_enable.assert_called_once()

    def test_forbidden_for_anonymous(self):
        resp = self.client.post(reverse("rfid-scan-deep"))
        self.assertNotEqual(resp.status_code, 200)


class DeepReadAuthTests(TestCase):
    class MockReader:
        MI_OK = 1
        MI_ERR = 2
        PICC_REQIDL = 0
        PICC_AUTHENT1A = 0x60
        PICC_AUTHENT1B = 0x61

        def __init__(self):
            self.auth_calls = []

        def MFRC522_Request(self, _):
            return (self.MI_OK, None)

        def MFRC522_Anticoll(self):
            return (self.MI_OK, [0xAA, 0xBB, 0xCC, 0xDD, 0xEE])

        def MFRC522_Auth(self, mode, block, key, uid):
            self.auth_calls.append(mode)
            return self.MI_ERR if mode == self.PICC_AUTHENT1A else self.MI_OK

        def MFRC522_Read(self, block):
            return (self.MI_OK, [0] * 16)

    @patch("core.notifications.notify_async")
    @patch("core.models.RFID.objects.get_or_create")
    def test_auth_tries_key_a_then_b(self, mock_get, mock_notify):
        tag = MagicMock(
            label_id=1,
            pk=1,
            allowed=True,
            color="B",
            released=False,
            reference=None,
        )
        mock_get.return_value = (tag, False)
        reader = self.MockReader()
        enable_deep_read(60)
        read_rfid(mfrc=reader, cleanup=False)
        self.assertGreaterEqual(len(reader.auth_calls), 2)
        self.assertEqual(reader.auth_calls[0], reader.PICC_AUTHENT1A)
        self.assertEqual(reader.auth_calls[1], reader.PICC_AUTHENT1B)


