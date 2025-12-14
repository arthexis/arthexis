from __future__ import annotations

import getpass
import io

from django.contrib.auth import authenticate
from django.core.management.base import BaseCommand, CommandError
from django.test.client import RequestFactory

from apps.users import temp_passwords


class Command(BaseCommand):
    """Interactively test authentication for a user."""

    help = "Prompt for credentials and attempt to authenticate a user."

    def add_arguments(self, parser):
        parser.add_argument(
            "username",
            nargs="?",
            help="Username or email address to test. Prompts if omitted.",
        )
        parser.add_argument(
            "--password",
            dest="password",
            help="Password or TOTP code to use. Prompts securely when omitted.",
        )
        parser.add_argument(
            "--host",
            default="127.0.0.1",
            help="Host header to use for the authentication request.",
        )

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        if not username:
            username = input("Username: ").strip()
        if not username:
            raise CommandError("A username or email address is required.")

        password = options.get("password")
        if password is None:
            password = getpass.getpass("Password or authenticator code: ")
        password = (password or "").strip()
        if not password:
            raise CommandError("No password or code provided.")

        expired_notice = self._check_temp_password_expiration(username)

        request = self._build_request(options.get("host") or "127.0.0.1")
        user = authenticate(request=request, username=username, password=password)
        if user is None:
            extra = f" {expired_notice}" if expired_notice else ""
            raise CommandError(f"Authentication failed for '{username}'.{extra}")

        backend = getattr(user, "backend", "") or "unknown backend"
        buffer = io.StringIO()
        buffer.write(
            f"Authentication succeeded for {user.get_username()} using {backend}.\n"
        )
        if expired_notice:
            buffer.write(f"Note: {expired_notice}\n")
        self.stdout.write(buffer.getvalue())

    def _check_temp_password_expiration(self, username: str) -> str | None:
        entry = temp_passwords.load_temp_password(username)
        if entry is None:
            return None
        if entry.is_expired:
            temp_passwords.discard_temp_password(username)
            return f"Temporary password expired at {entry.expires_at.isoformat()}."
        return None

    def _build_request(self, host: str):
        factory = RequestFactory()
        request = factory.post("/auth/test")
        request.META["HTTP_HOST"] = host
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.META.setdefault("SERVER_NAME", host)
        return request
