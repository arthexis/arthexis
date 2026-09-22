from django.test import SimpleTestCase

from apps.celery import manifest


class CeleryManifestTests(SimpleTestCase):
    def test_manifest_publishes_name_and_description(self) -> None:
        self.assertEqual(manifest.NAME, "celery")
        self.assertTrue(manifest.DESCRIPTION)
