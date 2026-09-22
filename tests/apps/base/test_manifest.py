from django.test import SimpleTestCase

from apps.base import manifest


class BaseManifestTests(SimpleTestCase):
    def test_manifest_publishes_name_and_description(self) -> None:
        self.assertEqual(manifest.NAME, "base")
        self.assertTrue(manifest.DESCRIPTION)
