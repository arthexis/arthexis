from django.test import SimpleTestCase

from apps.sigils import manifest


class SigilsManifestTests(SimpleTestCase):
    def test_manifest_publishes_name_and_description(self) -> None:
        self.assertEqual(manifest.NAME, "sigils")
        self.assertTrue(manifest.DESCRIPTION)
