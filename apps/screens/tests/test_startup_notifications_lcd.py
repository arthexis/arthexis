from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.nodes.models import Node, NodeFeature
from apps.screens.startup_notifications import lcd_feature_enabled_for_paths


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
            (lock_dir / "lcd_screen.lck").write_text("state=enabled\nbooting\nready\n")

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
            (lock_dir / "lcd_screen.lck").write_text("state=enabled\nbooting\nready\n")

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
            (lock_dir / "lcd_screen.lck").write_text("state=enabled\nbooting\nready\n")

            self.assertTrue(
                lcd_feature_enabled_for_paths(base_dir=base_dir, node_base_path=node_base_path)
            )

    def test_lcd_feature_enabled_for_paths_respects_disabled_state(self):
        with TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            node_base_path = base_dir / "work" / "nodes"
            lock_dir = node_base_path / ".locks"
            lock_dir.mkdir(parents=True)
            (lock_dir / "lcd_screen.lck").write_text(
                "state=disabled\nbooting\nready\n"
            )

            self.assertFalse(
                lcd_feature_enabled_for_paths(base_dir=base_dir, node_base_path=node_base_path)
            )

    def test_lcd_feature_enabled_for_paths_backfills_legacy_lock(self):
        with TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            lock_dir = base_dir / ".locks"
            lock_dir.mkdir(parents=True)
            (lock_dir / "lcd_screen_enabled.lck").write_text("")

            self.assertTrue(
                lcd_feature_enabled_for_paths(base_dir=base_dir, node_base_path=base_dir)
            )

            lock_file = lock_dir / "lcd_screen.lck"
            self.assertTrue(lock_file.exists())
            self.assertTrue(lock_file.read_text().startswith("state=enabled"))
