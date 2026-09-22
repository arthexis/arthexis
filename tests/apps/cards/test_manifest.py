from django.test import SimpleTestCase

from apps.cards import manifest


class CardsManifestTests(SimpleTestCase):
    def test_manifest_publishes_name_and_description(self) -> None:
        self.assertEqual(manifest.NAME, "cards")
        self.assertTrue(manifest.DESCRIPTION)
