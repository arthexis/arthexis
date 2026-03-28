from __future__ import annotations

import os
import sys
from getpass import getpass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.dns.models import DNSProviderCredential


class Command(BaseCommand):
    """Manage GoDaddy DNS credentials from the CLI."""

    help = "Add, remove, or list GoDaddy DNS credentials."

    def add_arguments(self, parser):
        """Register command-line arguments."""

        parser.add_argument(
            "action",
            nargs="?",
            choices=("add", "remove", "list", "setup"),
            default="list",
            help="Action to perform. Defaults to 'list'.",
        )
        parser.add_argument(
            "credential_id",
            nargs="?",
            type=int,
            help="Credential ID for remove action.",
        )
        parser.add_argument("--user", help="Username that owns the credential.")
        parser.add_argument("--api-key", help="GoDaddy API key.")
        parser.add_argument("--api-secret", help="GoDaddy API secret.")
        parser.add_argument(
            "--api-secret-file",
            help="Path to a file containing the GoDaddy API secret.",
        )
        parser.add_argument("--customer-id", default="", help="Optional GoDaddy customer ID.")
        parser.add_argument(
            "--default-domain",
            default="",
            help="Optional default domain used when record domains are omitted.",
        )
        parser.add_argument(
            "--sandbox",
            action="store_true",
            help="Use GoDaddy OTE sandbox endpoints.",
        )
        parser.add_argument(
            "--disabled",
            action="store_true",
            help="Create the credential in disabled state.",
        )

    def handle(self, *args, **options):
        """Dispatch to the selected sub-command action."""

        action = options["action"]
        if action == "add":
            self._handle_add(options)
            return
        if action == "remove":
            self._handle_remove(options)
            return
        if action == "setup":
            self._handle_setup(options)
            return
        self._handle_list()

    def _handle_add(self, options: dict[str, object]) -> None:
        """Create a new GoDaddy DNS credential."""

        username = str(options.get("user") or "").strip()
        user = self._resolve_user(username) if username else None
        api_key, api_secret = self._resolve_api_credentials(options)

        missing: list[str] = []
        if not username:
            missing.append("--user")
        if not api_key:
            missing.append("--api-key")
        if not api_secret:
            missing.append("--api-secret")
        if missing:
            raise CommandError(f"Missing required arguments for add: {', '.join(missing)}")

        credential = DNSProviderCredential.objects.create(
            user=user,
            provider=DNSProviderCredential.Provider.GODADDY,
            api_key=api_key,
            api_secret=api_secret,
            customer_id=str(options.get("customer_id") or "").strip(),
            default_domain=str(options.get("default_domain") or "").strip(),
            use_sandbox=bool(options.get("sandbox")),
            is_enabled=not bool(options.get("disabled")),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Added GoDaddy credential #{credential.pk} for user '{username}'."
            )
        )

    def _resolve_user(self, username: str):
        """Return a user for the provided username or raise CommandError."""

        user_model = get_user_model()
        try:
            return user_model.objects.get(username=username)
        except user_model.DoesNotExist as exc:
            raise CommandError(f"User '{username}' does not exist.") from exc

    def _resolve_api_credentials(self, options: dict[str, object]) -> tuple[str, str]:
        """Resolve GoDaddy API credentials using safe precedence rules."""

        sandbox_mode = bool(options.get("sandbox"))
        allow_cli_secret_flags = sandbox_mode or bool(getattr(settings, "DEBUG", False)) or (
            os.getenv("GODADDY_ALLOW_CLI_SECRETS", "").strip().lower() in {"1", "true", "yes", "on"}
        )

        api_key_flag = str(options.get("api_key") or "").strip()
        api_secret_flag = str(options.get("api_secret") or "").strip()
        api_secret_file = str(options.get("api_secret_file") or "").strip()

        if (api_key_flag or api_secret_flag) and not allow_cli_secret_flags:
            raise CommandError(
                "Passing --api-key/--api-secret is disabled for production safety. "
                "Use GODADDY_API_KEY/GODADDY_API_SECRET (or --api-secret-file), "
                "or set --sandbox/GODADDY_ALLOW_CLI_SECRETS=1 for non-production usage."
            )

        api_key = os.getenv("GODADDY_API_KEY", "").strip()
        if not api_key and api_key_flag and allow_cli_secret_flags:
            api_key = api_key_flag
        if not api_key and sys.stdin.isatty():
            api_key = input("GoDaddy API key: ").strip()

        api_secret = os.getenv("GODADDY_API_SECRET", "").strip()
        if not api_secret and api_secret_file:
            try:
                with open(api_secret_file, encoding="utf-8") as secret_file:
                    api_secret = secret_file.read().strip()
            except OSError as exc:
                raise CommandError(f"Unable to read --api-secret-file '{api_secret_file}': {exc}") from exc
        if not api_secret and api_secret_flag and allow_cli_secret_flags:
            api_secret = api_secret_flag
        if not api_secret and sys.stdin.isatty():
            api_secret = getpass("GoDaddy API secret: ").strip()

        missing: list[str] = []
        if not api_key:
            missing.append("API key (set GODADDY_API_KEY or use --api-key in sandbox)")
        if not api_secret:
            missing.append(
                "API secret (set GODADDY_API_SECRET, use --api-secret-file, or use --api-secret in sandbox)"
            )
        if missing:
            raise CommandError(f"Missing required arguments for add: {', '.join(missing)}")

        return api_key, api_secret

    def _handle_remove(self, options: dict[str, object]) -> None:
        """Delete a GoDaddy DNS credential by ID."""

        credential_id = options.get("credential_id")
        if credential_id is None:
            raise CommandError("remove requires credential_id. Usage: godaddy remove <credential_id>")

        deleted, _ = DNSProviderCredential.objects.filter(
            pk=credential_id,
            provider=DNSProviderCredential.Provider.GODADDY,
        ).delete()
        if not deleted:
            raise CommandError(f"GoDaddy credential #{credential_id} was not found.")

        self.stdout.write(self.style.SUCCESS(f"Removed GoDaddy credential #{credential_id}."))

    def _handle_setup(self, options: dict[str, object]) -> None:
        """Create or update a GoDaddy DNS credential from key/secret input."""

        api_key, api_secret = self._resolve_api_credentials(options)
        username = str(options.get("user") or "").strip()
        user = self._resolve_user(username) if username else None

        credential = (
            DNSProviderCredential.objects.filter(
                provider=DNSProviderCredential.Provider.GODADDY,
                api_key=api_key,
            )
            .order_by("pk")
            .first()
        )
        defaults = {
            "api_secret": api_secret,
            "customer_id": str(options.get("customer_id") or "").strip(),
            "default_domain": str(options.get("default_domain") or "").strip(),
            "use_sandbox": bool(options.get("sandbox")),
            "is_enabled": not bool(options.get("disabled")),
        }
        if user is not None:
            defaults["user"] = user

        if credential is None:
            credential = DNSProviderCredential.objects.create(
                provider=DNSProviderCredential.Provider.GODADDY,
                api_key=api_key,
                **defaults,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Configured GoDaddy credential #{credential.pk} for API key '{api_key}'."
                )
            )
            return

        updated_fields: list[str] = []
        for field, value in defaults.items():
            if getattr(credential, field) != value:
                setattr(credential, field, value)
                updated_fields.append(field)
        if updated_fields:
            credential.save(update_fields=updated_fields)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Updated GoDaddy credential #{credential.pk} for API key '{api_key}'."
                )
            )
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"GoDaddy credential #{credential.pk} for API key '{api_key}' is already up to date."
            )
        )

    def _handle_list(self) -> None:
        """Print GoDaddy DNS credentials."""

        credentials = list(DNSProviderCredential.objects.filter(
            provider=DNSProviderCredential.Provider.GODADDY,
        ).order_by("pk"))
        if not credentials:
            self.stdout.write("No GoDaddy credentials configured.")
            return

        for credential in credentials:
            owner = credential.owner_display() or "<no owner>"
            status = "enabled" if credential.is_enabled else "disabled"
            sandbox = "sandbox" if credential.use_sandbox else "production"
            default_domain = credential.get_default_domain() or "-"
            self.stdout.write(
                f"{credential.pk}: owner={owner} status={status} env={sandbox} default_domain={default_domain}"
            )
