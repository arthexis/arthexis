"""CLI utility for managing and verifying Evergo credentials."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.evergo.exceptions import EvergoAPIError
from apps.evergo.models import EvergoUser


class Command(BaseCommand):
    """Add/update Evergo credentials for a Suite user and optionally test login."""

    help = "Bind Evergo credentials to a Suite user profile and optionally test them."

    def add_arguments(self, parser):
        """Define command-line arguments used to upsert and verify credentials."""
        parser.add_argument("user", help="Suite username or email used to own the profile.")
        parser.add_argument("--email", dest="evergo_email", help="Evergo login email.")
        parser.add_argument("--password", dest="evergo_password", help="Evergo login password.")
        parser.add_argument(
            "--test",
            action="store_true",
            help="Attempt Evergo login and sync fields after saving credentials.",
        )

    def handle(self, *args, **options):
        """Execute command flow for profile creation, update, and credential testing."""
        suite_user = self._resolve_user(options["user"])
        profile, _ = EvergoUser.objects.get_or_create(user=suite_user)

        email = options.get("evergo_email")
        if email is not None:
            profile.evergo_email = email.strip()

        password = options.get("evergo_password")
        if password is not None:
            profile.evergo_password = password

        profile.full_clean()
        profile.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Evergo profile saved for suite user '{suite_user.get_username()}' (id={profile.pk})."
            )
        )

        if options.get("test"):
            try:
                result = profile.test_login()
            except EvergoAPIError as exc:
                raise CommandError(f"Evergo login test failed: {exc}") from exc

            self.stdout.write(
                self.style.SUCCESS(
                    "Evergo login successful "
                    f"(status={result.response_code}, evergo_user_id={profile.evergo_user_id})."
                )
            )

    def _resolve_user(self, identifier: str):
        """Fetch a Suite user by username or email, raising a command error when missing."""
        User = get_user_model()
        identifier = identifier.strip()
        user = User.objects.filter(username=identifier).first()
        if user is None:
            user = User.objects.filter(email__iexact=identifier).first()
        if user is None:
            raise CommandError(f"Suite user '{identifier}' was not found.")
        return user
