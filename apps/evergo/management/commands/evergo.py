"""CLI utility for managing and verifying Evergo credentials."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.evergo.exceptions import EvergoAPIError
from apps.evergo.models import EvergoUser


class Command(BaseCommand):
    """Add/update Evergo credentials for a Suite user and optionally test login."""

    help = "Bind Evergo credentials to a Suite user profile and optionally test them."

    def add_arguments(self, parser):
        """Define command-line arguments used to upsert and verify credentials."""
        parser.add_argument(
            "user", help="Suite username or email used to own the profile."
        )
        parser.add_argument("--email", dest="evergo_email", help="Evergo login email.")
        parser.add_argument(
            "--password", dest="evergo_password", help="Evergo login password."
        )
        parser.add_argument(
            "--test",
            action="store_true",
            help="Attempt Evergo login and sync fields after saving credentials.",
        )
        parser.add_argument(
            "--load-customers",
            action="store_true",
            help=(
                "Run the same customer/order sync workflow exposed in the admin load customers wizard."
            ),
        )
        parser.add_argument(
            "--queries",
            help=(
                "Free-form SO numbers and customer names, matching the admin wizard format."
            ),
        )
        parser.add_argument(
            "--queries-file",
            help="Path to a UTF-8 text file with SO numbers and/or customer names.",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=20,
            help="Evergo API timeout in seconds for login and load-customer calls.",
        )

    def handle(self, *args, **options):
        """Execute command flow for profile creation, update, and credential testing."""
        suite_user = self._resolve_user(options["user"])
        profile = EvergoUser.objects.filter(user=suite_user).order_by("pk").first()
        if profile is None:
            profile = EvergoUser(user=suite_user)

        email = options.get("evergo_email")
        if email is not None:
            profile.evergo_email = email.strip()

        password = options.get("evergo_password")
        if password is not None:
            profile.evergo_password = password

        is_load_customers = bool(options.get("load_customers"))
        raw_queries = self._resolve_queries(options) if is_load_customers else ""
        if is_load_customers and not profile.evergo_email:
            raise CommandError(
                "Evergo profile is missing evergo_email. Use --email to set it before loading customers."
            )

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

        if options.get("load_customers"):
            timeout = options.get("timeout") or 20
            if timeout <= 0:
                raise CommandError("--timeout must be greater than zero.")

            try:
                summary = profile.load_customers_from_queries(
                    raw_queries=raw_queries,
                    timeout=timeout,
                )
            except EvergoAPIError as exc:
                raise CommandError(f"Evergo customer load failed: {exc}") from exc

            unresolved = summary["unresolved"]
            unresolved_label = ", ".join(unresolved) if unresolved else "none"
            self.stdout.write(
                self.style.SUCCESS(
                    "Customer sync completed. "
                    f"Customers loaded: {summary['customers_loaded']} | "
                    f"Orders created: {summary['orders_created']} | "
                    f"Orders updated: {summary['orders_updated']} | "
                    f"Placeholders: {summary['placeholders_created']} | "
                    f"Unresolved: {unresolved_label}"
                )
            )

    def _resolve_user(self, identifier: str):
        """Fetch a Suite user by username or email, raising a command error when missing."""
        User = get_user_model()
        identifier = identifier.strip()
        user_by_username = User.objects.filter(username=identifier).first()
        user_by_email = User.objects.filter(email__iexact=identifier).first()

        if (
            user_by_username
            and user_by_email
            and user_by_username.pk != user_by_email.pk
        ):
            raise CommandError(
                f"Identifier '{identifier}' matches multiple users; use a unique username."
            )

        user = (
            User.objects.filter(Q(email__iexact=identifier) | Q(username=identifier))
            .order_by("pk")
            .first()
        )

        if "@" in identifier:
            user = user_by_email or user_by_username or user
        else:
            user = user_by_username or user_by_email or user

        if user is None:
            raise CommandError(f"Suite user '{identifier}' was not found.")
        return user

    def _resolve_queries(self, options: dict[str, object]) -> str:
        """Get free-form customer-load queries from CLI flags."""
        inline_queries = options.get("queries")
        queries_file = options.get("queries_file")

        if inline_queries and queries_file:
            raise CommandError("Use only one of --queries or --queries-file")

        if inline_queries:
            resolved = str(inline_queries).strip()
            if resolved:
                return resolved
            raise CommandError("--queries must contain at least one SO number or customer name.")

        if not queries_file:
            raise CommandError("--load-customers requires --queries or --queries-file.")

        try:
            with open(str(queries_file), encoding="utf-8") as handle:
                resolved = handle.read().strip()
        except FileNotFoundError as exc:
            raise CommandError(f"Queries file '{queries_file}' was not found.") from exc
        except OSError as exc:
            raise CommandError(f"Could not read queries file '{queries_file}': {exc}") from exc

        if not resolved:
            raise CommandError("--queries-file must contain at least one SO number or customer name.")
        return resolved
