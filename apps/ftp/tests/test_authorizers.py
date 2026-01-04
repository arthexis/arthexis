from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import TestCase
from pyftpdlib.authorizers import AuthenticationFailed

from ..authorizers import DjangoFTPAuthorizer
from ..utils import FolderBinding, UserMount


class DjangoFTPAuthorizerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ftpuser", password="pass1234"
        )

    def test_validates_credentials_and_permissions(self):
        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "data"
            target.mkdir()
            mount = UserMount(
                home=Path(tmpdir) / "home",
                bindings=[
                    FolderBinding(link_name="data", target=target, permissions="elr"),
                ],
                permissions="elr",
            )
            authorizer = DjangoFTPAuthorizer({self.user.username: mount})
            handler = type("Handler", (), {})()

            authorizer.validate_authentication(self.user.username, "pass1234", handler)
            self.assertTrue(hasattr(handler, "user_obj"))

            allowed_path = target / "file.txt"
            denied_path = target / "write.txt"
            self.assertTrue(authorizer.has_perm(self.user.username, "r", str(allowed_path)))
            self.assertFalse(authorizer.has_perm(self.user.username, "w", str(denied_path)))
            self.assertEqual(str(mount.home), authorizer.get_home_dir(self.user.username))

    def test_unknown_user_rejected(self):
        authorizer = DjangoFTPAuthorizer({})
        with self.assertRaises(AuthenticationFailed):
            authorizer.validate_authentication("missing", "password", object())
