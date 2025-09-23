import os
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from nodes.models import Node, NodeFeature, NodeRole
from core.system import _gather_info


class SystemInfoRoleTests(SimpleTestCase):
    @override_settings(NODE_ROLE="Terminal")
    def test_defaults_to_terminal(self):
        info = _gather_info()
        self.assertEqual(info["role"], "Terminal")

    @override_settings(NODE_ROLE="Satellite")
    def test_uses_settings_role(self):
        info = _gather_info()
        self.assertEqual(info["role"], "Satellite")


class SystemInfoScreenModeTests(SimpleTestCase):
    def test_without_lockfile(self):
        info = _gather_info()
        self.assertEqual(info["screen_mode"], "")

    def test_with_lockfile(self):
        lock_dir = Path(settings.BASE_DIR) / "locks"
        lock_dir.mkdir(exist_ok=True)
        lock_file = lock_dir / "screen_mode.lck"
        lock_file.write_text("tft")
        try:
            info = _gather_info()
            self.assertEqual(info["screen_mode"], "tft")
        finally:
            lock_file.unlink()
            if not any(lock_dir.iterdir()):
                lock_dir.rmdir()


class SystemInfoRevisionTests(SimpleTestCase):
    @patch("core.system.revision.get_revision", return_value="abcdef1234567890")
    def test_includes_full_revision(self, mock_revision):
        info = _gather_info()
        self.assertEqual(info["revision"], "abcdef1234567890")
        mock_revision.assert_called_once()


class SystemInfoRunserverDetectionTests(SimpleTestCase):
    @patch("core.system.subprocess.run")
    def test_detects_runserver_process_port(self, mock_run):
        mock_run.return_value = CompletedProcess(
            args=["pgrep"],
            returncode=0,
            stdout="123 python manage.py runserver 0.0.0.0:8000 --noreload\n",
        )

        info = _gather_info()

        self.assertTrue(info["running"])
        self.assertEqual(info["port"], 8000)

    @patch("core.system._probe_ports", return_value=(True, 8000))
    @patch("core.system.subprocess.run", side_effect=FileNotFoundError)
    def test_falls_back_to_port_probe_when_pgrep_missing(self, mock_run, mock_probe):
        info = _gather_info()

        self.assertTrue(info["running"])
        self.assertEqual(info["port"], 8000)

