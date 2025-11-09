import errno
import io
import json
import os
import subprocess
import sys
import types
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.utils import timezone

from pages.models import Application, Module
from nodes.models import Node, NodeRole

from core.models import RFID
from ocpp.rfid.reader import read_rfid, enable_deep_read, validate_rfid_value
from ocpp.rfid.detect import detect_scanner, main as detect_main
from ocpp.rfid import background_reader, camera
from ocpp.rfid.constants import (
    DEFAULT_IRQ_PIN,
    DEFAULT_RST_PIN,
    GPIO_PIN_MODE_BCM,
    MODULE_WIRING,
    SPI_BUS,
    SPI_DEVICE,
)


pytestmark = [pytest.mark.feature("rfid-scanner")]


class BackgroundReaderConfigurationTests(SimpleTestCase):
    def setUp(self):
        background_reader._auto_detect_logged = False

    def tearDown(self):
        background_reader._auto_detect_logged = False

    def test_is_configured_auto_detects_without_lock(self):
        fake_lock = Path("/tmp/rfid-auto-detect.lock")
        with (
            patch("ocpp.rfid.background_reader._lock_path", return_value=fake_lock),
            patch("ocpp.rfid.background_reader._has_spi_device", return_value=True),
            patch(
                "ocpp.rfid.background_reader._dependencies_available",
                return_value=True,
            ),
        ):
            self.assertTrue(background_reader.is_configured())

    def test_is_configured_requires_dependencies(self):
        fake_lock = Path("/tmp/rfid-auto-detect.lock")
        with (
            patch("ocpp.rfid.background_reader._lock_path", return_value=fake_lock),
            patch("ocpp.rfid.background_reader._has_spi_device", return_value=True),
            patch(
                "ocpp.rfid.background_reader._dependencies_available",
                return_value=False,
            ),
        ):
            self.assertFalse(background_reader.is_configured())


class ScanNextViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("rfid-user", password="pwd")
        self.client.force_login(self.user)

    @patch("config.middleware.Node.get_local", return_value=None)
    @patch("config.middleware.get_site")
    @patch(
        "ocpp.rfid.views.scan_sources",
        return_value={
            "rfid": "ABCD1234",
            "label_id": 1,
            "created": False,
            "kind": RFID.CLASSIC,
        },
    )
    def test_scan_next_success(self, mock_scan, mock_site, mock_node):
        resp = self.client.get(reverse("rfid-scan-next"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "rfid": "ABCD1234",
                "label_id": 1,
                "created": False,
                "kind": RFID.CLASSIC,
            },
        )

    @patch("config.middleware.Node.get_local", return_value=None)
    @patch("config.middleware.get_site")
    @patch("ocpp.rfid.views.scan_sources", return_value={"error": "boom"})
    def test_scan_next_error(self, mock_scan, mock_site, mock_node):
        resp = self.client.get(reverse("rfid-scan-next"))
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": "boom"})

    @patch("config.middleware.Node.get_local", return_value=None)
    @patch("config.middleware.get_site")
    @patch(
        "ocpp.rfid.views.validate_rfid_value",
        return_value={"rfid": "ABCD1234", "label_id": 1, "created": False},
    )
    def test_scan_next_post_validates(self, mock_validate, mock_site, mock_node):
        User = get_user_model()
        user = User.objects.create_user("scanner", password="pwd")
        self.client.force_login(user)
        resp = self.client.post(
            reverse("rfid-scan-next"),
            data=json.dumps({"rfid": "ABCD1234"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(), {"rfid": "ABCD1234", "label_id": 1, "created": False}
        )
        mock_validate.assert_called_once_with("ABCD1234", kind=None, endianness=None)

    @patch("config.middleware.Node.get_local", return_value=None)
    @patch("config.middleware.get_site")
    @patch("ocpp.rfid.views.validate_rfid_value")
    def test_scan_next_post_requires_authentication(
        self, mock_validate, mock_site, mock_node
    ):
        self.client.logout()
        resp = self.client.post(
            reverse("rfid-scan-next"),
            data=json.dumps({"rfid": "ABCD1234"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json(), {"error": "Authentication required"})
        mock_validate.assert_not_called()

    @patch("config.middleware.Node.get_local", return_value=None)
    @patch("config.middleware.get_site")
    def test_scan_next_post_invalid_json(self, mock_site, mock_node):
        User = get_user_model()
        user = User.objects.create_user("invalid-json", password="pwd")
        self.client.force_login(user)
        resp = self.client.post(
            reverse("rfid-scan-next"),
            data="{",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "Invalid JSON payload"})

    def test_scan_next_requires_authentication(self):
        self.client.logout()
        resp = self.client.get(reverse("rfid-scan-next"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("pages:login"), resp.url)


class ReaderNotificationTests(TestCase):
    def setUp(self):
        super().setUp()
        self.queue_patcher = patch("ocpp.rfid.reader.queue_camera_snapshot")
        self.mock_queue = self.queue_patcher.start()

    def tearDown(self):
        self.queue_patcher.stop()
        super().tearDown()

    def _mock_reader(self):
        class MockReader:
            MI_OK = 1
            PICC_REQIDL = 0

            def MFRC522_Request(self, _):
                return (self.MI_OK, None)

            def MFRC522_Anticoll(self):
                return (self.MI_OK, [0xAB, 0xCD, 0x12, 0x34, 0x56])

            def MFRC522_SelectTag(self, _uid):
                self.select_called = True
                return self.MI_OK

            def MFRC522_StopCrypto1(self):
                self.stop_called = True

        return MockReader()

    @patch("ocpp.rfid.reader.notify_async")
    @patch("ocpp.rfid.reader.RFID.register_scan")
    def test_notify_on_allowed_tag(self, mock_register, mock_notify):
        reference = MagicMock(value="https://example.com")
        tag = MagicMock(
            label_id=1,
            pk=1,
            allowed=True,
            color="B",
            released=False,
            reference=reference,
        )
        mock_register.return_value = (tag, False)

        reader = self._mock_reader()
        result = read_rfid(mfrc=reader, cleanup=False)
        self.assertEqual(result["label_id"], 1)
        self.assertEqual(result["kind"], RFID.CLASSIC)
        self.assertEqual(result["reference"], "https://example.com")
        self.assertEqual(mock_notify.call_count, 1)
        mock_notify.assert_has_calls([call("RFID 1 OK", f"{result['rfid']} B")])
        self.assertTrue(getattr(reader, "select_called", False))
        self.assertTrue(getattr(reader, "stop_called", False))

    @patch("ocpp.rfid.reader.notify_async")
    @patch("ocpp.rfid.reader.RFID.register_scan")
    def test_notify_on_disallowed_tag(self, mock_register, mock_notify):
        tag = MagicMock(
            label_id=2,
            pk=2,
            allowed=False,
            color="B",
            released=False,
            reference=None,
        )
        mock_register.return_value = (tag, False)

        reader = self._mock_reader()
        result = read_rfid(mfrc=reader, cleanup=False)
        self.assertEqual(result["kind"], RFID.CLASSIC)
        self.assertEqual(mock_notify.call_count, 1)
        mock_notify.assert_has_calls([call("RFID 2 BAD", f"{result['rfid']} B")])
        self.assertTrue(getattr(reader, "select_called", False))
        self.assertTrue(getattr(reader, "stop_called", False))

    @patch("ocpp.rfid.reader.notify_async")
    @patch("ocpp.rfid.reader.RFID.register_scan")
    def test_snapshot_metadata_passed_to_queue(self, mock_register, mock_notify):
        tag = MagicMock(
            label_id=5,
            pk=5,
            allowed=True,
            color="",
            released=False,
            reference=None,
        )
        mock_register.return_value = (tag, True)

        reader = self._mock_reader()
        result = read_rfid(mfrc=reader, cleanup=False)

        self.assertTrue(self.mock_queue.called)
        args, kwargs = self.mock_queue.call_args
        self.assertEqual(args[0], result["rfid"])
        self.assertEqual(args[1]["label_id"], tag.pk)
        self.assertTrue(args[1]["created"])


class CameraSnapshotTriggerTests(SimpleTestCase):
    @patch("ocpp.rfid.camera._camera_feature_enabled", return_value=False)
    @patch("ocpp.rfid.camera.threading.Thread")
    def test_queue_skips_without_feature(self, mock_thread, mock_enabled):
        camera.queue_camera_snapshot("ABC123", {"label_id": 1})
        mock_thread.assert_not_called()

    @patch("ocpp.rfid.camera.timezone.now")
    @patch("ocpp.rfid.camera.threading.Thread")
    @patch("ocpp.rfid.camera._camera_feature_enabled", return_value=True)
    def test_queue_spawns_thread_with_metadata(
        self, mock_enabled, mock_thread, mock_now
    ):
        thread_instance = MagicMock()
        mock_thread.return_value = thread_instance
        mock_now.return_value = datetime(2023, 1, 1, tzinfo=dt_timezone.utc)

        camera.queue_camera_snapshot("ABC123", {"label_id": 7})

        mock_thread.assert_called_once()
        kwargs = mock_thread.call_args.kwargs
        self.assertTrue(kwargs.get("daemon"))
        metadata = kwargs["args"][0]
        self.assertEqual(metadata["rfid"], "ABC123")
        self.assertEqual(metadata["label_id"], 7)
        self.assertEqual(metadata["source"], "rfid-scan")
        self.assertEqual(metadata["captured_at"], "2023-01-01T00:00:00+00:00")
        thread_instance.start.assert_called_once()

    @patch("ocpp.rfid.camera.close_old_connections")
    @patch("ocpp.rfid.camera.save_screenshot")
    @patch("ocpp.rfid.camera.capture_rpi_snapshot")
    def test_worker_saves_snapshot(
        self, mock_capture, mock_save, mock_close
    ):
        mock_capture.return_value = Path("/tmp/test.jpg")

        camera._capture_snapshot_worker({"rfid": "ABC", "label_id": 3})

        mock_capture.assert_called_once()
        mock_save.assert_called_once()
        _, kwargs = mock_save.call_args
        self.assertEqual(kwargs["method"], "RFID_SCAN")
        metadata = json.loads(kwargs["content"])
        self.assertEqual(metadata["rfid"], "ABC")
        self.assertEqual(metadata["label_id"], 3)
        self.assertGreaterEqual(mock_close.call_count, 2)

    @patch("ocpp.rfid.camera.close_old_connections")
    @patch("ocpp.rfid.camera.save_screenshot")
    @patch("ocpp.rfid.camera.capture_rpi_snapshot", side_effect=RuntimeError("boom"))
    def test_worker_handles_capture_failure(
        self, mock_capture, mock_save, mock_close
    ):
        camera._capture_snapshot_worker({"rfid": "XYZ"})
        mock_save.assert_not_called()
        self.assertGreaterEqual(mock_close.call_count, 2)


class ValidateRfidValueTests(SimpleTestCase):
    @patch("ocpp.rfid.reader.timezone.now")
    @patch("ocpp.rfid.reader.notify_async")
    @patch("ocpp.rfid.reader.RFID.register_scan")
    def test_creates_new_tag(self, mock_register, mock_notify, mock_now):
        fake_now = object()
        mock_now.return_value = fake_now
        tag = MagicMock()
        tag.pk = 1
        tag.label_id = 1
        tag.allowed = True
        tag.color = "B"
        tag.released = False
        tag.reference = None
        tag.kind = RFID.CLASSIC
        tag.endianness = RFID.BIG_ENDIAN
        mock_register.return_value = (tag, True)

        result = validate_rfid_value("abcd1234")

        mock_register.assert_called_once_with(
            "ABCD1234", kind=None, endianness=RFID.BIG_ENDIAN
        )
        tag.save.assert_called_once_with(update_fields=["last_seen_on"])
        self.assertIs(tag.last_seen_on, fake_now)
        mock_notify.assert_called_once_with("RFID 1 OK", "ABCD1234 B")
        self.assertTrue(result["created"])
        self.assertEqual(result["rfid"], "ABCD1234")
        self.assertEqual(result["endianness"], RFID.BIG_ENDIAN)

    @patch("ocpp.rfid.reader.timezone.now")
    @patch("ocpp.rfid.reader.notify_async")
    @patch("ocpp.rfid.reader.RFID.register_scan")
    def test_updates_existing_tag_kind(self, mock_register, mock_notify, mock_now):
        fake_now = object()
        mock_now.return_value = fake_now
        tag = MagicMock()
        tag.pk = 5
        tag.label_id = 5
        tag.allowed = False
        tag.color = "G"
        tag.released = True
        tag.reference = None
        tag.kind = RFID.CLASSIC
        tag.endianness = RFID.BIG_ENDIAN
        mock_register.return_value = (tag, False)

        result = validate_rfid_value("abcd", kind=RFID.NTAG215)

        mock_register.assert_called_once_with(
            "ABCD", kind=RFID.NTAG215, endianness=RFID.BIG_ENDIAN
        )
        tag.save.assert_called_once_with(update_fields=["kind", "last_seen_on"])
        self.assertIs(tag.last_seen_on, fake_now)
        self.assertEqual(tag.kind, RFID.NTAG215)
        mock_notify.assert_called_once_with("RFID 5 BAD", "ABCD G")
        self.assertFalse(result["allowed"])
        self.assertFalse(result["created"])
        self.assertEqual(result["kind"], RFID.NTAG215)
        self.assertEqual(result["endianness"], RFID.BIG_ENDIAN)

    @patch("ocpp.rfid.reader.timezone.now")
    @patch("ocpp.rfid.reader.notify_async")
    @patch("ocpp.rfid.reader.RFID.register_scan")
    def test_registers_little_endian_value(
        self, mock_register, mock_notify, mock_now
    ):
        fake_now = object()
        mock_now.return_value = fake_now
        tag = MagicMock()
        tag.pk = 7
        tag.label_id = 7
        tag.allowed = True
        tag.color = "B"
        tag.released = False
        tag.reference = None
        tag.kind = RFID.CLASSIC
        tag.endianness = RFID.LITTLE_ENDIAN
        mock_register.return_value = (tag, True)

        result = validate_rfid_value("A1B2C3D4", endianness=RFID.LITTLE_ENDIAN)

        mock_register.assert_called_once_with(
            "D4C3B2A1", kind=None, endianness=RFID.LITTLE_ENDIAN
        )
        tag.save.assert_called_once_with(update_fields=["last_seen_on"])
        self.assertEqual(result["rfid"], "D4C3B2A1")
        self.assertEqual(result["endianness"], RFID.LITTLE_ENDIAN)
        mock_notify.assert_called_once()

    def test_rejects_invalid_value(self):
        result = validate_rfid_value("invalid!")
        self.assertEqual(result, {"error": "RFID must be hexadecimal digits"})

    def test_rejects_non_string_values(self):
        result = validate_rfid_value(12345)
        self.assertEqual(result, {"error": "RFID must be a string"})

    def test_rejects_missing_value(self):
        result = validate_rfid_value(None)
        self.assertEqual(result, {"error": "RFID value is required"})


    @patch("ocpp.rfid.reader.timezone.now")
    @patch("ocpp.rfid.reader.notify_async")
    @patch("ocpp.rfid.reader.subprocess.Popen")
    @patch("ocpp.rfid.reader.subprocess.run")
    @patch("ocpp.rfid.reader.RFID.register_scan")
    def test_external_command_success(
        self, mock_register, mock_run, mock_popen, mock_notify, mock_now
    ):
        fake_now = object()
        mock_now.return_value = fake_now
        tag = MagicMock()
        tag.pk = 1
        tag.label_id = 1
        tag.allowed = True
        tag.external_command = "echo ok"
        tag.color = "B"
        tag.released = False
        tag.reference = None
        tag.kind = RFID.CLASSIC
        tag.endianness = RFID.BIG_ENDIAN
        mock_register.return_value = (tag, False)
        mock_run.return_value = types.SimpleNamespace(
            returncode=0, stdout="ok\n", stderr=""
        )
        mock_popen.return_value = object()

        result = validate_rfid_value("abcd1234")

        mock_run.assert_called_once()
        run_args, run_kwargs = mock_run.call_args
        self.assertEqual(run_args[0], "echo ok")
        self.assertTrue(run_kwargs.get("shell"))
        env = run_kwargs.get("env", {})
        self.assertEqual(env.get("RFID_VALUE"), "ABCD1234")
        self.assertEqual(env.get("RFID_LABEL_ID"), "1")
        self.assertEqual(env.get("RFID_ENDIANNESS"), RFID.BIG_ENDIAN)
        mock_popen.assert_not_called()
        mock_notify.assert_called_once_with("RFID 1 OK", "ABCD1234 B")
        tag.save.assert_called_once_with(update_fields=["last_seen_on"])
        self.assertTrue(result["allowed"])
        output = result.get("command_output")
        self.assertIsInstance(output, dict)
        self.assertEqual(output.get("stdout"), "ok\n")
        self.assertEqual(output.get("stderr"), "")
        self.assertEqual(output.get("returncode"), 0)
        self.assertEqual(output.get("error"), "")
        self.assertEqual(result["endianness"], RFID.BIG_ENDIAN)

    @patch("ocpp.rfid.reader.timezone.now")
    @patch("ocpp.rfid.reader.notify_async")
    @patch("ocpp.rfid.reader.subprocess.Popen")
    @patch("ocpp.rfid.reader.subprocess.run")
    @patch("ocpp.rfid.reader.RFID.register_scan")
    def test_external_command_failure_blocks_tag(
        self, mock_register, mock_run, mock_popen, mock_notify, mock_now
    ):
        fake_now = object()
        mock_now.return_value = fake_now
        tag = MagicMock()
        tag.pk = 2
        tag.label_id = 2
        tag.allowed = True
        tag.external_command = "exit 1"
        tag.color = "G"
        tag.released = False
        tag.reference = None
        tag.kind = RFID.CLASSIC
        tag.endianness = RFID.BIG_ENDIAN
        mock_register.return_value = (tag, False)
        mock_run.return_value = types.SimpleNamespace(
            returncode=1, stdout="", stderr="failure"
        )
        mock_popen.return_value = object()

        result = validate_rfid_value("ffff")

        mock_run.assert_called_once()
        mock_notify.assert_called_once_with("RFID 2 BAD", "FFFF G")
        tag.save.assert_called_once_with(update_fields=["last_seen_on"])
        self.assertFalse(result["allowed"])
        output = result.get("command_output")
        self.assertIsInstance(output, dict)
        self.assertEqual(output.get("returncode"), 1)
        self.assertEqual(output.get("stdout"), "")
        self.assertEqual(output.get("stderr"), "failure")
        self.assertEqual(output.get("error"), "")
        mock_popen.assert_not_called()
        self.assertEqual(result["endianness"], RFID.BIG_ENDIAN)

    @patch("ocpp.rfid.reader.timezone.now")
    @patch("ocpp.rfid.reader.notify_async")
    @patch("ocpp.rfid.reader.subprocess.Popen")
    @patch("ocpp.rfid.reader.subprocess.run")
    @patch("ocpp.rfid.reader.RFID.register_scan")
    def test_external_command_strips_trailing_percent_tokens(
        self, mock_register, mock_run, mock_popen, mock_notify, mock_now
    ):
        mock_now.return_value = timezone.now()
        tag = MagicMock()
        tag.pk = 3
        tag.label_id = 3
        tag.allowed = True
        tag.external_command = "echo weird"
        tag.color = "Y"
        tag.released = False
        tag.reference = None
        tag.kind = RFID.CLASSIC
        tag.endianness = RFID.BIG_ENDIAN
        mock_register.return_value = (tag, False)
        mock_run.return_value = types.SimpleNamespace(
            returncode=0,
            stdout="first %\nsecond 50%\r\nthird % %\n",
            stderr="oops %\n",
        )

        result = validate_rfid_value("abc3")

        output = result.get("command_output")
        self.assertIsNotNone(output)
        self.assertEqual(
            output.get("stdout"), "first\nsecond 50%\r\nthird\n"
        )
        self.assertEqual(output.get("stderr"), "oops\n")
        self.assertEqual(output.get("returncode"), 0)
        self.assertEqual(output.get("error"), "")
        mock_popen.assert_not_called()

    @patch("ocpp.rfid.reader.timezone.now")
    @patch("ocpp.rfid.reader.notify_async")
    @patch("ocpp.rfid.reader.subprocess.Popen")
    @patch("ocpp.rfid.reader.subprocess.run")
    @patch("ocpp.rfid.reader.RFID.register_scan")
    def test_external_command_error_strips_trailing_percent_tokens(
        self, mock_register, mock_run, mock_popen, mock_notify, mock_now
    ):
        mock_now.return_value = timezone.now()
        tag = MagicMock()
        tag.pk = 4
        tag.label_id = 4
        tag.allowed = True
        tag.external_command = "echo boom"
        tag.color = "R"
        tag.released = False
        tag.reference = None
        tag.kind = RFID.CLASSIC
        tag.endianness = RFID.BIG_ENDIAN
        mock_register.return_value = (tag, False)
        mock_run.side_effect = RuntimeError("bad % %")

        result = validate_rfid_value("abcd")

        output = result.get("command_output")
        self.assertIsInstance(output, dict)
        self.assertEqual(output.get("stdout"), "")
        self.assertEqual(output.get("stderr"), "")
        self.assertEqual(output.get("error"), "bad")
        self.assertFalse(result["allowed"])
        mock_popen.assert_not_called()

    @patch("ocpp.rfid.reader.timezone.now")
    @patch("ocpp.rfid.reader.notify_async")
    @patch("ocpp.rfid.reader.subprocess.Popen")
    @patch("ocpp.rfid.reader.subprocess.run")
    @patch("ocpp.rfid.reader.RFID.register_scan")
    def test_post_command_runs_after_success(
        self, mock_register, mock_run, mock_popen, mock_notify, mock_now
    ):
        fake_now = object()
        mock_now.return_value = fake_now
        tag = MagicMock()
        tag.pk = 3
        tag.label_id = 3
        tag.allowed = True
        tag.external_command = ""
        tag.post_auth_command = "echo done"
        tag.color = "B"
        tag.released = False
        tag.reference = None
        tag.kind = RFID.CLASSIC
        tag.endianness = RFID.BIG_ENDIAN
        mock_register.return_value = (tag, False)
        result = validate_rfid_value("abcdef")

        mock_run.assert_not_called()
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], "echo done")
        env = kwargs.get("env", {})
        self.assertEqual(env.get("RFID_VALUE"), "ABCDEF")
        self.assertEqual(env.get("RFID_LABEL_ID"), "3")
        self.assertEqual(env.get("RFID_ENDIANNESS"), RFID.BIG_ENDIAN)
        self.assertIs(kwargs.get("stdout"), subprocess.DEVNULL)
        self.assertIs(kwargs.get("stderr"), subprocess.DEVNULL)
        self.assertTrue(result["allowed"])
        mock_notify.assert_called_once_with("RFID 3 OK", "ABCDEF B")
        self.assertEqual(result["endianness"], RFID.BIG_ENDIAN)


class CardTypeDetectionTests(TestCase):
    def _mock_ntag_reader(self):
        class MockReader:
            MI_OK = 1
            PICC_REQIDL = 0

            def MFRC522_Request(self, _):
                return (self.MI_OK, None)

            def MFRC522_Anticoll(self):
                return (
                    self.MI_OK,
                    [0x04, 0xD3, 0x2A, 0x1B, 0x5F, 0x23, 0x19],
                )

            def MFRC522_SelectTag(self, _uid):
                self.select_called = True
                return self.MI_OK

            def MFRC522_StopCrypto1(self):
                self.stop_called = True

        return MockReader()

    @patch("ocpp.rfid.reader.notify_async")
    @patch("ocpp.rfid.reader.RFID.register_scan")
    def test_detects_ntag215(self, mock_register, _mock_notify):
        tag = MagicMock(
            pk=1,
            label_id=1,
            allowed=True,
            color="B",
            released=False,
            reference=None,
            kind=RFID.NTAG215,
        )
        mock_register.return_value = (tag, True)
        reader = self._mock_ntag_reader()
        result = read_rfid(mfrc=reader, cleanup=False)
        self.assertEqual(result["kind"], RFID.NTAG215)
        self.assertTrue(getattr(reader, "select_called", False))
        self.assertTrue(getattr(reader, "stop_called", False))


class RFIDLastSeenTests(TestCase):
    def _mock_reader(self):
        class MockReader:
            MI_OK = 1
            PICC_REQIDL = 0

            def MFRC522_Request(self, _):
                return (self.MI_OK, None)

            def MFRC522_Anticoll(self):
                return (self.MI_OK, [0xAB, 0xCD, 0x12, 0x34])

            def MFRC522_SelectTag(self, _uid):
                self.select_called = True
                return self.MI_OK

            def MFRC522_StopCrypto1(self):
                self.stop_called = True

        return MockReader()

    @patch("ocpp.rfid.reader.notify_async")
    def test_last_seen_updated_on_read(self, _mock_notify):
        tag = RFID.objects.create(rfid="ABCD1234")
        reader = self._mock_reader()
        result = read_rfid(mfrc=reader, cleanup=False)
        tag.refresh_from_db()
        self.assertIsNotNone(tag.last_seen_on)
        self.assertEqual(result["kind"], RFID.CLASSIC)
        self.assertTrue(getattr(reader, "select_called", False))
        self.assertTrue(getattr(reader, "stop_called", False))


class RFIDDetectionScriptTests(SimpleTestCase):
    @patch("ocpp.rfid.detect._ensure_django")
    @patch("ocpp.rfid.detect._lockfile_status", return_value=(False, None))
    @patch(
        "ocpp.rfid.irq_wiring_check.check_irq_pin",
        return_value={"irq_pin": DEFAULT_IRQ_PIN},
    )
    def test_detect_scanner_success(self, mock_check, _mock_lock, _mock_setup):
        result = detect_scanner()
        self.assertEqual(
            result,
            {
                "detected": True,
                "irq_pin": DEFAULT_IRQ_PIN,
            },
        )
        mock_check.assert_called_once()

    @patch("ocpp.rfid.detect._ensure_django")
    @patch("ocpp.rfid.detect._lockfile_status", return_value=(False, None))
    @patch(
        "ocpp.rfid.irq_wiring_check.check_irq_pin",
        return_value={"error": "no scanner detected"},
    )
    def test_detect_scanner_failure(self, mock_check, _mock_lock, _mock_setup):
        result = detect_scanner()
        self.assertFalse(result["detected"])
        self.assertEqual(result["reason"], "no scanner detected")
        mock_check.assert_called_once()

    @patch("ocpp.rfid.detect._ensure_django")
    @patch(
        "ocpp.rfid.detect._lockfile_status",
        return_value=(True, Path("/locks/rfid.lck")),
    )
    @patch(
        "ocpp.rfid.irq_wiring_check.check_irq_pin",
        return_value={"error": "no scanner detected"},
    )
    def test_detect_scanner_assumed_with_lock(self, mock_check, _mock_lock, _mock_setup):
        result = detect_scanner()
        self.assertTrue(result["detected"])
        self.assertTrue(result["assumed"])
        self.assertEqual(result["reason"], "no scanner detected")
        self.assertEqual(result["lockfile"], "/locks/rfid.lck")
        mock_check.assert_called_once()

    @patch("ocpp.rfid.detect._ensure_django")
    @patch("ocpp.rfid.detect._lockfile_status", return_value=(False, None))
    def test_detect_scanner_busy_assumed(self, _mock_lock, _mock_setup):
        with (
            patch("ocpp.rfid.irq_wiring_check._setup_hardware", return_value=False),
            patch(
                "ocpp.rfid.irq_wiring_check.read_rfid",
                return_value={
                    "error": "Device or resource busy",
                    "errno": errno.EBUSY,
                },
            ),
        ):
            result = detect_scanner()

        self.assertTrue(result["detected"])
        self.assertTrue(result["assumed"])
        self.assertTrue(result["busy"])
        self.assertEqual(result["reason"], "Device or resource busy")
        self.assertIsNone(result.get("irq_pin"))
        self.assertEqual(result.get("errno"), errno.EBUSY)

    @patch(
        "ocpp.rfid.detect.detect_scanner",
        return_value={"detected": True, "irq_pin": DEFAULT_IRQ_PIN},
    )
    def test_detect_main_success_output(self, mock_detect):
        buffer = io.StringIO()
        with patch("sys.stdout", new=buffer):
            exit_code = detect_main([])
        self.assertEqual(exit_code, 0)
        self.assertIn("IRQ pin", buffer.getvalue())
        mock_detect.assert_called_once()

    @patch(
        "ocpp.rfid.detect.detect_scanner",
        return_value={"detected": False, "reason": "missing hardware"},
    )
    def test_detect_main_failure_output(self, mock_detect):
        buffer = io.StringIO()
        with patch("sys.stdout", new=buffer):
            exit_code = detect_main([])
        self.assertEqual(exit_code, 1)
        self.assertIn("missing hardware", buffer.getvalue())
        mock_detect.assert_called_once()

    @patch(
        "ocpp.rfid.detect.detect_scanner",
        return_value={
            "detected": True,
            "assumed": True,
            "reason": "no scanner detected",
            "lockfile": "/locks/rfid.lck",
        },
    )
    def test_detect_main_assumed_output(self, mock_detect):
        buffer = io.StringIO()
        with patch("sys.stdout", new=buffer):
            exit_code = detect_main([])
        self.assertEqual(exit_code, 0)
        self.assertIn("assumed active", buffer.getvalue())
        self.assertIn("/locks/rfid.lck", buffer.getvalue())
        mock_detect.assert_called_once()

    @patch(
        "ocpp.rfid.detect.detect_scanner",
        return_value={
            "detected": True,
            "assumed": True,
            "busy": True,
            "reason": "Device or resource busy",
        },
    )
    def test_detect_main_busy_output(self, mock_detect):
        buffer = io.StringIO()
        with patch("sys.stdout", new=buffer):
            exit_code = detect_main([])
        self.assertEqual(exit_code, 0)
        output = buffer.getvalue()
        self.assertIn("assumed active", output)
        self.assertIn("Device or resource busy", output)
        mock_detect.assert_called_once()


class RFIDLockFileUsageTests(SimpleTestCase):
    @patch("ocpp.rfid.background_reader.is_configured", return_value=True)
    def test_queue_result_marks_lock(self, _mock_config):
        with patch(
            "ocpp.rfid.background_reader._tag_queue.get",
            return_value={"rfid": "ABC"},
        ) as mock_get, patch(
            "ocpp.rfid.background_reader._mark_scanner_used"
        ) as mock_mark:
            result = background_reader.get_next_tag()
        self.assertEqual(result["rfid"], "ABC")
        mock_get.assert_called_once()
        mock_mark.assert_called_once()

    @patch("ocpp.rfid.background_reader.is_configured", return_value=True)
    def test_direct_read_marks_lock(self, _mock_config):
        with (
            patch(
                "ocpp.rfid.background_reader._tag_queue.get",
                side_effect=background_reader.queue.Empty,
            ) as mock_get,
            patch(
                "ocpp.rfid.reader.read_rfid",
                return_value={"rfid": "XYZ"},
            ) as mock_read,
            patch("ocpp.rfid.background_reader._mark_scanner_used") as mock_mark,
        ):
            result = background_reader.get_next_tag()
        self.assertEqual(result["rfid"], "XYZ")
        mock_get.assert_called_once()
        mock_read.assert_called_once()
        mock_mark.assert_called_once()


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
            module.landings.filter(path="/ocpp/rfid/validator/").exists()
        )


class ScannerTemplateTests(TestCase):
    def setUp(self):
        self.url = reverse("rfid-reader")
        User = get_user_model()
        self.user = User.objects.create_user("scanner-user", password="pwd")

    def test_configure_link_for_staff(self):
        User = get_user_model()
        staff = User.objects.create_user("staff", password="pwd", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'id="rfid-configure"')
        self.assertContains(resp, 'id="rfid-connect-local"')
        self.assertNotContains(resp, 'Restart &amp; Test Scanner')

    def test_redirect_for_anonymous(self):
        self.client.logout()
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("pages:login"), resp.url)

    def test_advanced_fields_for_staff(self):
        User = get_user_model()
        staff = User.objects.create_user("staff2", password="pwd", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'id="rfid-kind"')
        self.assertContains(resp, 'id="rfid-rfid"')
        self.assertContains(resp, 'id="rfid-released"')
        self.assertContains(resp, 'id="rfid-reference"')
        self.assertContains(resp, 'id="rfid-deep-details"')

    def test_basic_fields_for_authenticated_user(self):
        self.client.logout()
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'id="rfid-kind"')
        self.assertNotContains(resp, 'id="rfid-connect-local"')
        self.assertNotContains(resp, 'id="rfid-rfid"')
        self.assertNotContains(resp, 'id="rfid-released"')
        self.assertNotContains(resp, 'id="rfid-reference"')
        self.assertNotContains(resp, 'id="rfid-deep-details"')
        self.assertNotContains(resp, 'Restart &amp; Test Scanner')

    def test_deep_read_button_for_staff(self):
        User = get_user_model()
        staff = User.objects.create_user("staff3", password="pwd", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'id="rfid-deep-read"')

    def test_no_deep_read_button_for_authenticated_user(self):
        self.client.logout()
        self.client.force_login(self.user)
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
    @patch(
        "ocpp.rfid.views.enable_deep_read_mode",
        return_value={"status": "deep read enabled", "enabled": True},
    )
    def test_enable_deep_read(self, mock_enable, mock_site, mock_node):
        User = get_user_model()
        staff = User.objects.create_user("staff4", password="pwd", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.post(reverse("rfid-scan-deep"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(), {"status": "deep read enabled", "enabled": True}
        )
        mock_enable.assert_called_once()

    def test_forbidden_for_anonymous(self):
        resp = self.client.post(reverse("rfid-scan-deep"))
        self.assertNotEqual(resp.status_code, 200)


class DeepReadAuthTests(TestCase):
    def setUp(self):
        super().setUp()
        self.queue_patcher = patch("ocpp.rfid.reader.queue_camera_snapshot")
        self.queue_patcher.start()

    def tearDown(self):
        self.queue_patcher.stop()
        super().tearDown()

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
    @patch("ocpp.rfid.reader.RFID.register_scan")
    def test_auth_tries_key_a_then_b(self, mock_register, mock_notify):
        tag = MagicMock(
            label_id=1,
            pk=1,
            allowed=True,
            color="B",
            released=False,
            reference=None,
        )
        tag.key_a = "A1A2A3A4A5A6"
        tag.key_b = "B1B2B3B4B5B6"
        tag.key_a_verified = True
        tag.key_b_verified = False
        tag.data = []
        tag.save = MagicMock()
        mock_register.return_value = (tag, False)
        reader = self.MockReader()
        enable_deep_read(60)
        result = read_rfid(mfrc=reader, cleanup=False)
        self.assertGreaterEqual(len(reader.auth_calls), 2)
        self.assertEqual(reader.auth_calls[0], reader.PICC_AUTHENT1A)
        self.assertEqual(reader.auth_calls[1], reader.PICC_AUTHENT1B)
        self.assertTrue(result.get("deep_read"))
        self.assertIn("dump", result)
        self.assertIn("keys", result)
        self.assertEqual(result["keys"].get("a"), "A1A2A3A4A5A6")
        self.assertTrue(result["keys"].get("b_verified"))
        self.assertTrue(tag.key_b_verified)
        self.assertTrue(any(entry.get("key") == "B" for entry in result["dump"]))
        self.assertEqual(tag.data, result["dump"])
        self.assertTrue(
            any(
                "data" in set(kwargs.get("update_fields", []) or [])
                for _args, kwargs in tag.save.call_args_list
            )
        )

    @patch("core.notifications.notify_async")
    @patch("ocpp.rfid.reader.RFID.register_scan")
    def test_heuristic_verifies_unverified_keys(
        self, mock_register, mock_notify
    ):
        tag = MagicMock(
            label_id=1,
            pk=1,
            allowed=True,
            color="B",
            released=False,
            reference=None,
        )
        tag.key_a = "111111111111"
        tag.key_b = "FFFFFFFFFFFF"
        tag.key_a_verified = False
        tag.key_b_verified = True
        tag.save = MagicMock()
        mock_register.return_value = (tag, False)

        class HeuristicReader:
            MI_OK = 1
            MI_ERR = 2
            PICC_REQIDL = 0
            PICC_AUTHENT1A = 0x60
            PICC_AUTHENT1B = 0x61

            def __init__(self):
                self.auth_calls: list[tuple[int, str]] = []

            def MFRC522_Request(self, _):
                return (self.MI_OK, None)

            def MFRC522_Anticoll(self):
                return (self.MI_OK, [0x01, 0x02, 0x03, 0x04])

            def MFRC522_Auth(self, mode, block, key, uid):
                key_hex = "".join(f"{value:02X}" for value in key)
                self.auth_calls.append((mode, key_hex))
                if mode == self.PICC_AUTHENT1A and key_hex == "A0A1A2A3A4A5":
                    return self.MI_OK
                return self.MI_ERR

            def MFRC522_Read(self, block):
                return (self.MI_OK, list(range(16)))

        reader = HeuristicReader()
        enable_deep_read(60)
        result = read_rfid(mfrc=reader, cleanup=False)

        self.assertIn((reader.PICC_AUTHENT1A, "A0A1A2A3A4A5"), reader.auth_calls)
        self.assertEqual(result["keys"].get("a"), "A0A1A2A3A4A5")
        self.assertTrue(result["keys"].get("a_verified"))
        self.assertEqual(tag.key_a, "A0A1A2A3A4A5")
        self.assertTrue(tag.key_a_verified)
        self.assertIn(
            call(update_fields=["key_a", "key_a_verified"]),
            tag.save.call_args_list,
        )


class RFIDWiringConfigTests(SimpleTestCase):
    def test_module_wiring_map(self):
        expected = [
            ("SDA", "CE0"),
            ("SCK", "SCLK"),
            ("MOSI", "MOSI"),
            ("MISO", "MISO"),
            ("IRQ", "IO4"),
            ("GND", "GND"),
            ("RST", "IO25"),
            ("3v3", "3v3"),
        ]
        self.assertEqual(list(MODULE_WIRING.items()), expected)
        self.assertEqual(DEFAULT_IRQ_PIN, 4)
        self.assertEqual(DEFAULT_RST_PIN, 25)

    def test_background_reader_uses_default_irq_pin(self):
        self.assertEqual(background_reader.IRQ_PIN, DEFAULT_IRQ_PIN)

    def test_reader_instantiation_uses_configured_pins(self):
        class DummyReader:
            init_args = None
            init_kwargs = None

            def __init__(self, *args, **kwargs):
                DummyReader.init_args = args
                DummyReader.init_kwargs = kwargs
                self.MI_OK = 1
                self.PICC_REQIDL = 0

        fake_mfrc = types.ModuleType("mfrc522")
        fake_mfrc.MFRC522 = DummyReader
        fake_gpio = types.ModuleType("RPi.GPIO")
        fake_rpi = types.ModuleType("RPi")
        fake_rpi.GPIO = fake_gpio

        with patch.dict(
            "sys.modules",
            {"mfrc522": fake_mfrc, "RPi": fake_rpi, "RPi.GPIO": fake_gpio},
        ):
            result = read_rfid(timeout=0, cleanup=False)

        self.assertEqual(result, {"rfid": None, "label_id": None})
        self.assertIsNotNone(DummyReader.init_kwargs)
        self.assertEqual(DummyReader.init_kwargs["bus"], SPI_BUS)
        self.assertEqual(DummyReader.init_kwargs["device"], SPI_DEVICE)
        self.assertEqual(DummyReader.init_kwargs["pin_mode"], GPIO_PIN_MODE_BCM)
        self.assertEqual(DummyReader.init_kwargs["pin_rst"], DEFAULT_RST_PIN)
