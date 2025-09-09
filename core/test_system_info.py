import os
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.conf import settings
from django.test import SimpleTestCase, override_settings
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
