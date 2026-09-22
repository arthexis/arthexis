from django.test import SimpleTestCase

from apps.nodes import manifest


class NodesManifestTests(SimpleTestCase):
    def test_manifest_publishes_name_and_description(self) -> None:
        self.assertEqual(manifest.NAME, "nodes")
        self.assertTrue(manifest.DESCRIPTION)
