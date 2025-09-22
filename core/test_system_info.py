import os
from pathlib import Path
from tempfile import TemporaryDirectory

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


class SystemInfoFeatureTests(TestCase):
    @override_settings(NODE_ROLE="Terminal")
    def test_features_include_expected_and_actual(self):
        role = NodeRole.objects.create(name="Terminal")

        expected_only = NodeFeature.objects.create(
            slug="rfid-scanner", display="RFID Scanner"
        )
        expected_only.roles.add(role)

        both = NodeFeature.objects.create(
            slug="celery-queue", display="Celery Queue"
        )
        both.roles.add(role)

        NodeFeature.objects.create(slug="lcd-screen", display="LCD Screen")

        with TemporaryDirectory() as tmpdir:
            base_path = Path(tmpdir)
            locks_dir = base_path / "locks"
            locks_dir.mkdir()
            (locks_dir / "celery.lck").touch()
            (locks_dir / "lcd_screen.lck").touch()

            Node.objects.create(
                hostname="local",
                address="127.0.0.1",
                port=8000,
                mac_address=Node.get_current_mac(),
                role=role,
                base_path=str(base_path),
            )

            info = _gather_info()

        features = {feature["slug"]: feature for feature in info["features"]}

        self.assertEqual(
            set(features.keys()), {"celery-queue", "lcd-screen", "rfid-scanner"}
        )
        self.assertTrue(features["celery-queue"]["expected"])
        self.assertTrue(features["celery-queue"]["actual"])
        self.assertFalse(features["lcd-screen"]["expected"])
        self.assertTrue(features["lcd-screen"]["actual"])
        self.assertTrue(features["rfid-scanner"]["expected"])
        self.assertFalse(features["rfid-scanner"]["actual"])
