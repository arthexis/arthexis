from __future__ import annotations

import io
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core import temp_passwords


class Command(BaseCommand):
    """Create a temporary password for the requested user."""

    help = "Generate a temporary password for a user by username or email."

    def add_arguments(self, parser):
        parser.add_argument(
            "identifier",
            help="Username or email address identifying the user.",
        )
        parser.add_argument(
            "--expires-in",
            type=int,
            default=int(temp_passwords.DEFAULT_EXPIRATION.total_seconds()),
            help=(
                "Number of seconds before the temporary password expires. "
                "Defaults to 3600 (1 hour)."
            ),
        )

    def handle(self, *args, **options):
        identifier = options["identifier"]
        expires_in = int(options["expires_in"])
        if expires_in <= 0:
            raise CommandError("Expiration must be a positive number of seconds.")

        User = get_user_model()
        manager = getattr(User, "all_objects", User._default_manager)

        users = self._resolve_users(manager, identifier)
        if not users:
            raise CommandError(f"No user found for identifier {identifier!r}.")
        if len(users) > 1:
            usernames = ", ".join(sorted({user.username for user in users}))
            raise CommandError(
                "Multiple users share this email address. Provide the username "
                f"instead. Matches: {usernames}"
            )

        user = users[0]
        password = temp_passwords.generate_password()
        expires_at = timezone.now() + timedelta(seconds=expires_in)
        entry = temp_passwords.store_temp_password(user.username, password, expires_at)

        buffer = io.StringIO()
        buffer.write(f"Temporary password for {user.username}: {password}\n")
        buffer.write(f"Expires at: {entry.expires_at.isoformat()}\n")
        if not user.is_active:
            buffer.write("The account will be activated on first use.\n")
        self.stdout.write(buffer.getvalue())
        self.stdout.write(self.style.SUCCESS("Temporary password created."))

    def _resolve_users(self, manager, identifier):
        if "@" in identifier and not identifier.startswith("@"):
            queryset = manager.filter(email__iexact=identifier)
        else:
            queryset = manager.filter(username__iexact=identifier)
            if not queryset.exists():
                queryset = manager.filter(email__iexact=identifier)
        return list(queryset.order_by("username"))

