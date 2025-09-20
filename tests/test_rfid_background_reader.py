from django.test import TestCase
from django.apps import apps
from pathlib import Path
from django.conf import settings
from unittest.mock import patch

import pytest


pytestmark = [
    pytest.mark.role("Control"),
    pytest.mark.feature("rfid-scanner"),
]


class RFIDBackgroundReaderTests(TestCase):
    def setUp(self):
        self.lock = Path(settings.BASE_DIR) / "locks" / "control.lck"
        self.rfid_lock = Path(settings.BASE_DIR) / "locks" / "rfid.lck"
        self.lock.parent.mkdir(exist_ok=True)
        for lf in (self.lock, self.rfid_lock):
            if lf.exists():
                lf.unlink()

    def tearDown(self):
        for lf in (self.lock, self.rfid_lock):
            if lf.exists():
                lf.unlink()

    def _call_ready(self):
        app_config = apps.get_app_config("ocpp")
        app_config.ready()

    def test_start_not_called_without_control_lock(self):
        with patch("ocpp.rfid.background_reader.start") as mock_start:
            self._call_ready()
            self.assertFalse(mock_start.called)

    def test_start_not_called_without_rfid_lock(self):
        self.lock.touch()
        with patch("ocpp.rfid.background_reader.start") as mock_start:
            self._call_ready()
            self.assertFalse(mock_start.called)

    def test_start_called_with_lock(self):
        self.lock.touch()
        self.rfid_lock.touch()
        with patch("ocpp.rfid.background_reader.start") as mock_start:
            self._call_ready()
            self.assertTrue(mock_start.called)
