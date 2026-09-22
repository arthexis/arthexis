from django.test import SimpleTestCase

from apps.events import manifest


class EventsManifestTests(SimpleTestCase):
    def test_manifest_publishes_name_and_description(self) -> None:
        self.assertEqual(manifest.NAME, "events")
        self.assertTrue(manifest.DESCRIPTION)
