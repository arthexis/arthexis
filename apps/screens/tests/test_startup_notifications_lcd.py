from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.nodes.models import Node, NodeFeature
from apps.screens.startup_notifications import (
    LCD_HIGH_LOCK_FILE,
    LCD_LEGACY_FEATURE_LOCK,
    LCD_LOW_LOCK_FILE,
    lcd_feature_enabled,
    lcd_feature_enabled_for_paths,
    read_lcd_lock_file,
    render_lcd_lock_file,
)


class LCDStartupNotificationTests(TestCase):
    def _create_node(self, mac: str) -> Node:
        return Node.objects.create(
            hostname="local",
            port=8888,
            mac_address=mac,
            public_endpoint="local",
        )

    def test_lcd_feature_enabled_for_paths_checks_project_lock_dir(self):
        mac_address = "aa:bb:cc:dd:ee:ff"
        with TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            lock_dir = base_dir / ".locks"
            lock_dir.mkdir(parents=True)
            (lock_dir / LCD_HIGH_LOCK_FILE).write_text(
                "startup\nmessage\n", encoding="utf-8"
            )

            node = self._create_node(mac_address)
            feature = NodeFeature.objects.create(slug="lcd-screen", display="LCD Screen")

            with override_settings(BASE_DIR=base_dir):
                with patch("apps.nodes.models.Node.get_local", return_value=node):
                    self.assertTrue(feature.is_enabled)

    def test_refresh_features_detects_project_lock_dir_via_lcd_helper(self):
        mac_address = "aa:bb:cc:dd:ee:ff"
        with TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            lock_dir = base_dir / ".locks"
            lock_dir.mkdir(parents=True)
            (lock_dir / LCD_LOW_LOCK_FILE).write_text(
                "booting\n", encoding="utf-8"
            )

            feature = NodeFeature.objects.create(slug="lcd-screen", display="LCD Screen")
            node = self._create_node(mac_address)

            with override_settings(BASE_DIR=base_dir):
                with patch("apps.nodes.models.Node.get_current_mac", return_value=mac_address):
                    node.refresh_features()
                    self.assertIn(feature, node.features.all())

    def test_lcd_feature_enabled_for_paths_checks_node_lock_dir(self):
        with TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            node_base_path = base_dir / "work" / "nodes"
            lock_dir = node_base_path / ".locks"
            lock_dir.mkdir(parents=True)
            (lock_dir / LCD_LOW_LOCK_FILE).write_text(
                "booting\n", encoding="utf-8"
            )

            self.assertTrue(
                lcd_feature_enabled_for_paths(base_dir=base_dir, node_base_path=node_base_path)
            )

    def test_lcd_feature_disabled_when_missing(self):
        with TemporaryDirectory() as tmpdir:
            lock_dir = Path(tmpdir) / ".locks"
            lock_dir.mkdir(parents=True)

            self.assertFalse(lcd_feature_enabled(lock_dir))

    def test_lcd_feature_enabled_creates_lock_from_legacy_file(self):
        with TemporaryDirectory() as tmpdir:
            lock_dir = Path(tmpdir) / ".locks"
            lock_dir.mkdir(parents=True)
            (lock_dir / LCD_LEGACY_FEATURE_LOCK).write_text("", encoding="utf-8")

            self.assertTrue(lcd_feature_enabled(lock_dir))

    def test_render_and_read_preserve_expiration(self):
        expires_at = timezone.now().replace(microsecond=0)
        payload = render_lcd_lock_file(subject="hi", body="there", expires_at=expires_at)

        with TemporaryDirectory() as tmpdir:
            lock_dir = Path(tmpdir) / ".locks"
            lock_dir.mkdir(parents=True)
            target = lock_dir / LCD_HIGH_LOCK_FILE
            target.write_text(payload, encoding="utf-8")

            message = read_lcd_lock_file(target)

        assert message is not None
        assert message.expires_at == expires_at
