import json
import os
from datetime import timedelta
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from nodes.models import Node, NodeFeature, NodeRole
from core.system import (
    _gather_info,
    _load_auto_upgrade_log_entries,
    _read_auto_upgrade_mode,
    get_system_sigil_values,
)
from core.auto_upgrade_failover import failover_lock_path, read_failover_status


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


class SystemInfoModeTests(SimpleTestCase):
    def test_public_mode_case_insensitive(self):
        lock_dir = Path(settings.BASE_DIR) / "locks"
        lock_dir.mkdir(exist_ok=True)
        lock_file = lock_dir / "nginx_mode.lck"
        lock_file.write_text("PUBLIC", encoding="utf-8")
        try:
            info = _gather_info()
            self.assertEqual(info["mode"], "public")
            self.assertEqual(info["port"], 8888)
        finally:
            lock_file.unlink()
            if not any(lock_dir.iterdir()):
                lock_dir.rmdir()


class SystemInfoPortLockTests(SimpleTestCase):
    def test_uses_backend_port_lock_when_present(self):
        lock_dir = Path(settings.BASE_DIR) / "locks"
        lock_dir.mkdir(exist_ok=True)
        port_file = lock_dir / "backend_port.lck"
        port_file.write_text("9010", encoding="utf-8")
        try:
            info = _gather_info()
            self.assertEqual(info["port"], 9010)
        finally:
            port_file.unlink()
            if not any(lock_dir.iterdir()):
                lock_dir.rmdir()


class SystemInfoRevisionTests(SimpleTestCase):
    @patch("core.system.revision.get_revision", return_value="abcdef1234567890")
    def test_includes_full_revision(self, mock_revision):
        info = _gather_info()
        self.assertEqual(info["revision"], "abcdef1234567890")
        mock_revision.assert_called_once()


class SystemInfoDatabaseTests(SimpleTestCase):
    def test_collects_database_definitions(self):
        info = _gather_info()
        self.assertIn("databases", info)
        aliases = {entry["alias"] for entry in info["databases"]}
        self.assertIn("default", aliases)

    @override_settings(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": Path("/tmp/db.sqlite3"),
            }
        }
    )
    def test_serializes_path_database_names(self):
        info = _gather_info()
        databases = info["databases"]
        self.assertEqual(databases[0]["name"], "/tmp/db.sqlite3")


class AutoUpgradeModeTests(SimpleTestCase):
    def test_lock_file_read_error_marks_enabled(self):
        mock_path = Mock()
        mock_path.exists.return_value = True
        mock_path.read_text.side_effect = OSError

        with patch("core.system._auto_upgrade_mode_file", return_value=mock_path):
            info = _read_auto_upgrade_mode(Path("/tmp"))

        self.assertTrue(info["lock_exists"])
        self.assertTrue(info["enabled"])
        self.assertTrue(info["read_error"])


class AutoUpgradeLogParsingTests(SimpleTestCase):
    def test_parses_zulu_timestamp_entries(self):
        with TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            log_dir = base_dir / "logs"
            log_dir.mkdir()
            log_path = log_dir / "auto-upgrade.log"
            log_path.write_text("2024-01-01T12:34:56Z Started\n", encoding="utf-8")

            with patch("core.system._format_timestamp", return_value="formatted") as mock_format:
                result = _load_auto_upgrade_log_entries(base_dir)

        entries = result["entries"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["message"], "Started")
        self.assertEqual(entry["timestamp"], "formatted")

        mock_format.assert_called_once()
        parsed_dt = mock_format.call_args[0][0]
        self.assertEqual(parsed_dt.year, 2024)
        self.assertEqual(parsed_dt.month, 1)
        self.assertEqual(parsed_dt.day, 1)
        self.assertEqual(parsed_dt.utcoffset(), timedelta(0))


class AutoUpgradeFailoverStatusTests(SimpleTestCase):
    def test_read_failover_status_returns_none_when_missing(self):
        with TemporaryDirectory() as tmpdir:
            status = read_failover_status(Path(tmpdir))

        self.assertIsNone(status)

    def test_read_failover_status_parses_payload(self):
        with TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            lock_path = failover_lock_path(base_dir)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "reason": "Health check failed",
                "detail": "failed with timeout",
                "revision": "abcdef0",
                "created": timezone.now().isoformat(),
            }
            lock_path.write_text(json.dumps(payload), encoding="utf-8")
            status = read_failover_status(base_dir)

        self.assertIsNotNone(status)
        assert status is not None  # help mypy
        self.assertEqual(status.reason, "Health check failed")
        self.assertEqual(status.detail, "failed with timeout")
        self.assertEqual(status.revision, "abcdef0")
        self.assertIsNotNone(status.created)


class SystemInfoRunserverDetectionTests(SimpleTestCase):
    @patch("core.system.subprocess.run")
    def test_detects_runserver_process_port(self, mock_run):
        mock_run.return_value = CompletedProcess(
            args=["pgrep"],
            returncode=0,
            stdout="123 python manage.py runserver 0.0.0.0:8888 --noreload\n",
        )

        info = _gather_info()

        self.assertTrue(info["running"])
        self.assertEqual(info["port"], 8888)

    @patch("core.system._probe_ports", return_value=(True, 8888))
    @patch("core.system.subprocess.run", side_effect=FileNotFoundError)
    def test_falls_back_to_port_probe_when_pgrep_missing(self, mock_run, mock_probe):
        info = _gather_info()

        self.assertTrue(info["running"])
        self.assertEqual(info["port"], 8888)

    @patch("core.system._probe_ports", return_value=(False, None))
    @patch("core.system.subprocess.run")
    def test_runserver_fallbacks_to_backend_port_lock(self, mock_run, mock_probe):
        lock_dir = Path(settings.BASE_DIR) / "locks"
        lock_dir.mkdir(exist_ok=True)
        port_file = lock_dir / "backend_port.lck"
        port_file.write_text("9042", encoding="utf-8")
        mock_run.return_value = CompletedProcess(
            args=["pgrep"],
            returncode=0,
            stdout="123 python manage.py runserver --noreload\n",
        )

        try:
            info = _gather_info()
        finally:
            port_file.unlink()
            if not any(lock_dir.iterdir()):
                lock_dir.rmdir()

        self.assertTrue(info["running"])
        self.assertEqual(info["port"], 9042)


class SystemSigilValueTests(SimpleTestCase):
    def test_exports_values_for_sigil_resolution(self):
        sample_info = {
            "installed": True,
            "revision": "abcdef",
            "service": "gunicorn",
            "mode": "internal",
            "port": 8888,
            "role": "Terminal",
            "screen_mode": "",
            "features": [
                {"display": "Feature", "expected": True, "actual": False, "slug": "feature"}
            ],
            "running": True,
            "service_status": "active",
            "hostname": "example.local",
            "ip_addresses": ["127.0.0.1"],
            "databases": [
                {
                    "alias": "default",
                    "engine": "django.db.backends.sqlite3",
                    "name": "db.sqlite3",
                }
            ],
        }
        with patch("core.system._gather_info", return_value=sample_info):
            values = get_system_sigil_values()

        self.assertEqual(values["REVISION"], "abcdef")
        self.assertEqual(values["RUNNING"], "True")
        self.assertEqual(values["NGINX_MODE"], "internal (8888)")
        self.assertEqual(values["IP_ADDRESSES"], "127.0.0.1")
        features = json.loads(values["FEATURES"])
        self.assertEqual(features[0]["display"], "Feature")
        databases = json.loads(values["DATABASES"])
        self.assertEqual(databases[0]["alias"], "default")

