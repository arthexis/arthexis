from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase

from apps.features.models import Feature
from utils.extension_features import (
    load_extension_suite_features,
    sync_extension_suite_features,
)


MANIFEST = """\
[extension]
name = "printer-zebra"
repository = "arthexis/arthexis-printer-zebra"
django_apps = ["arthexis_printer_zebra"]
requires_apps = ["apps.printers"]

[[suite_features]]
slug = "zebra-label-printing"
display = "Zebra Label Printing"
main_app = "printers"
summary = "Adds Zebra label printing to the core printers app."
enabled_by_default = false
"""


class ExtensionSuiteFeatureTests(TestCase):
    def _write_manifest(self, base_dir: Path) -> None:
        checkout = base_dir / "extensions" / "arthexis-printer-zebra"
        checkout.mkdir(parents=True)
        (checkout / "arthexis-extension.toml").write_text(
            MANIFEST,
            encoding="utf-8",
        )

    def test_loads_feature_owned_by_existing_core_app(self):
        with TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            self._write_manifest(base_dir)

            definitions = load_extension_suite_features(base_dir)

        self.assertEqual(len(definitions), 1)
        definition = definitions[0]
        self.assertEqual(definition.slug, "zebra-label-printing")
        self.assertEqual(definition.main_app, "printers")
        self.assertFalse(definition.enabled_by_default)

    def test_sync_creates_feature_without_enabling_it(self):
        with TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            self._write_manifest(base_dir)

            created, updated = sync_extension_suite_features(base_dir)

        self.assertEqual((created, updated), (1, 0))
        feature = Feature.objects.get(slug="zebra-label-printing")
        self.assertEqual(feature.main_app.name, "printers")
        self.assertEqual(feature.metadata["extension"], "printer-zebra")
        self.assertFalse(feature.is_enabled)

    def test_resync_preserves_operator_enablement(self):
        with TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            self._write_manifest(base_dir)
            sync_extension_suite_features(base_dir)
            feature = Feature.objects.get(slug="zebra-label-printing")
            feature.set_enabled(True)

            created, updated = sync_extension_suite_features(base_dir)

        self.assertEqual(created, 0)
        self.assertEqual(updated, 0)
        feature.refresh_from_db()
        self.assertTrue(feature.is_enabled)
