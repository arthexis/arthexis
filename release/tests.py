from django.test import SimpleTestCase

from . import Credentials, DEFAULT_PACKAGE


class CredentialsTests(SimpleTestCase):
    def test_token_args(self):
        c = Credentials(token="abc")
        self.assertEqual(c.twine_args(), ["--username", "__token__", "--password", "abc"])

    def test_userpass_args(self):
        c = Credentials(username="u", password="p")
        self.assertEqual(c.twine_args(), ["--username", "u", "--password", "p"])

    def test_missing(self):
        c = Credentials()
        with self.assertRaises(ValueError):
            c.twine_args()


class PackageTests(SimpleTestCase):
    def test_default_name(self):
        self.assertEqual(DEFAULT_PACKAGE.name, "arthexis")
