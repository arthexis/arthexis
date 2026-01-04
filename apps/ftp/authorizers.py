from __future__ import annotations

from pathlib import Path

from django.contrib.auth import authenticate
from pyftpdlib.authorizers import AuthenticationFailed, DummyAuthorizer

from .utils import UserMount


class DjangoFTPAuthorizer(DummyAuthorizer):
    """Authorize FTP operations using Django users and folder permissions."""

    def __init__(self, mounts: dict[str, UserMount]):
        super().__init__()
        self.mounts = mounts

    def validate_authentication(self, username, password, handler):
        mount = self.mounts.get(username)
        if not mount:
            raise AuthenticationFailed("Access denied")

        user = authenticate(username=username, password=password)
        if user is None or not getattr(user, "is_active", False):
            raise AuthenticationFailed("Invalid credentials")
        handler.user_obj = user

    def get_home_dir(self, username):
        mount = self.mounts.get(username)
        if not mount:
            raise AuthenticationFailed("Unknown user")
        return str(mount.home)

    def has_perm(self, username, perm, path=None):
        mount = self.mounts.get(username)
        if not mount or perm not in mount.permissions:
            return False
        if path is None:
            return True
        path_obj = Path(path).resolve()
        for binding in mount.bindings:
            try:
                if path_obj.is_relative_to(binding.target.resolve()):
                    return perm in binding.permissions
            except FileNotFoundError:
                continue
        return perm in mount.permissions

    def get_perms(self, username):
        mount = self.mounts.get(username)
        return mount.permissions if mount else ""

    def get_msg_login(self, username):  # pragma: no cover - trivial
        return f"User {username} logged in."

    def get_msg_quit(self, username):  # pragma: no cover - trivial
        return f"Goodbye {username}."
