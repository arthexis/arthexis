from __future__ import annotations

"""Create MCP API keys for users."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.mcp.models import McpApiKey


class Command(BaseCommand):
    """Generate an MCP API key for a specific user."""

    help = "Create an MCP API key for a user with optional expiration."

    def add_arguments(self, parser) -> None:
        """Register command-line arguments for API key generation."""

        parser.add_argument(
            "--username",
            required=True,
            help="Username that will own the generated API key.",
        )
        parser.add_argument(
            "--label",
            default="default",
            help="Human-friendly label used to identify this key.",
        )
        parser.add_argument(
            "--expires-in-days",
            type=int,
            default=90,
            help="Optional expiration in days. Use 0 to create a non-expiring key.",
        )

    def handle(self, *args, **options):  # type: ignore[override]
        """Generate and print a new user-scoped MCP API key."""

        username = options["username"]
        label = options["label"].strip()
        expires_in_days = options["expires_in_days"]

        if not label:
            raise CommandError("--label must not be empty.")
        if expires_in_days < 0:
            raise CommandError("--expires-in-days must be zero or greater.")

        user_model = get_user_model()
        try:
            user = user_model.objects.get(username=username)
        except user_model.DoesNotExist as exc:
            raise CommandError(f"User '{username}' does not exist.") from exc

        expires_at = None
        if expires_in_days > 0:
            expires_at = timezone.now() + timedelta(days=expires_in_days)

        _api_key, plain_key = McpApiKey.objects.create_for_user(
            user=user,
            label=label,
            expires_at=expires_at,
        )

        self.stdout.write(self.style.SUCCESS("MCP API key created."))
        self.stdout.write(f"username={username}")
        self.stdout.write(f"label={label}")
        expires_at_text = expires_at.isoformat() if expires_at else "never"
        self.stdout.write(f"expires_at={expires_at_text}")
        self.stdout.write(f"api_key={plain_key}")
