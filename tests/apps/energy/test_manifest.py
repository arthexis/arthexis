from django.test import SimpleTestCase

from apps.energy import manifest


class EnergyManifestTests(SimpleTestCase):
    def test_manifest_publishes_name_and_description(self) -> None:
        self.assertEqual(manifest.NAME, "energy")
        self.assertTrue(manifest.DESCRIPTION)
