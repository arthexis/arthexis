from django.test import TestCase
from django.apps import apps
from pathlib import Path
from django.conf import settings
from unittest.mock import patch


class RFIDBackgroundReaderTests(TestCase):
    def setUp(self):
        self.lock = Path(settings.BASE_DIR) / "locks" / "control.lck"
        self.lock.parent.mkdir(exist_ok=True)
        if self.lock.exists():
            self.lock.unlink()

    def tearDown(self):
        if self.lock.exists():
            self.lock.unlink()

    def _call_ready(self):
        app_config = apps.get_app_config("ocpp")
        app_config.ready()

    def test_start_not_called_without_lock(self):
        with patch("ocpp.rfid.background_reader.start") as mock_start:
            self._call_ready()
            self.assertFalse(mock_start.called)

    def test_start_called_with_lock(self):
        self.lock.touch()
        with patch("ocpp.rfid.background_reader.start") as mock_start:
            self._call_ready()
            self.assertTrue(mock_start.called)
