from __future__ import annotations

import getpass
import os

from django.core.management.base import BaseCommand, CommandError

from apps.dns.models import DNSProviderCredential


class Command(BaseCommand):
    """List and update GoDaddy DNS provider credentials."""

    help = "List and edit GoDaddy DNS provider credentials."

    def add_arguments(self, parser):
        """Register CLI arguments for listing and editing credentials."""

        parser.add_argument(
            "--credential-id",
            type=int,
            help="Credential ID to update.",
        )
        parser.add_argument(
            "--api-key",
            help=(
                "Set API key directly (discouraged because command-line arguments can be "
                "visible to other users)."
            ),
        )
        parser.add_argument(
            "--api-secret",
            help=(
                "Set API secret directly (discouraged because command-line arguments can be "
                "visible to other users)."
            ),
        )
        parser.add_argument(
            "--api-key-env",
            help="Read API key from the named environment variable.",
        )
        parser.add_argument(
            "--api-secret-env",
            help="Read API secret from the named environment variable.",
        )
        parser.add_argument(
            "--prompt-for-secrets",
            action="store_true",
            help="Prompt securely for API key and API secret.",
        )
        parser.add_argument("--customer-id", help="Set customer ID.")
        parser.add_argument("--default-domain", help="Set default domain.")

        enabled_group = parser.add_mutually_exclusive_group()
        enabled_group.add_argument(
            "--enable",
            action="store_true",
            help="Enable the selected credential.",
        )
        enabled_group.add_argument(
            "--disable",
            action="store_true",
            help="Disable the selected credential.",
        )

        sandbox_group = parser.add_mutually_exclusive_group()
        sandbox_group.add_argument(
            "--sandbox",
            action="store_true",
            help="Use GoDaddy sandbox (OTE) API for the selected credential.",
        )
        sandbox_group.add_argument(
            "--no-sandbox",
            action="store_true",
            help="Use production GoDaddy API for the selected credential.",
        )

    def handle(self, *args, **options):
        """Update a specific credential when flags are set, then print all configs."""

        updates = self._collect_updates(options)
        credential_id = options.get("credential_id")

        if updates and credential_id is None:
            raise CommandError("--credential-id is required when using edit flags.")

        if credential_id is not None:
            self._update_credential(credential_id, updates)

        self._render_listing()

    def _collect_updates(self, options: dict) -> dict:
        """Build a dict of updated fields from parsed command options."""

        self._validate_secret_sources(options)

        updates: dict[str, object] = {}
        text_fields = ("customer_id", "default_domain")
        for field_name in text_fields:
            value = options.get(field_name)
            if value is not None:
                updates[field_name] = value

        self._collect_secret_updates(options, updates)

        if options.get("enable"):
            updates["is_enabled"] = True
        if options.get("disable"):
            updates["is_enabled"] = False
        if options.get("sandbox"):
            updates["use_sandbox"] = True
        if options.get("no_sandbox"):
            updates["use_sandbox"] = False

        return updates

    def _validate_secret_sources(self, options: dict) -> None:
        """Validate mutually exclusive and secure secret input combinations."""

        api_key_sources = sum(
            bool(options.get(source))
            for source in ("api_key", "api_key_env", "prompt_for_secrets")
        )
        if api_key_sources > 1:
            raise CommandError(
                "Choose only one API key source: --api-key, --api-key-env, or "
                "--prompt-for-secrets."
            )

        api_secret_sources = sum(
            bool(options.get(source))
            for source in ("api_secret", "api_secret_env", "prompt_for_secrets")
        )
        if api_secret_sources > 1:
            raise CommandError(
                "Choose only one API secret source: --api-secret, --api-secret-env, or "
                "--prompt-for-secrets."
            )

    def _collect_secret_updates(self, options: dict, updates: dict[str, object]) -> None:
        """Collect API key and secret updates from configured sources."""

        api_key_env = options.get("api_key_env")
        if api_key_env:
            updates["api_key"] = self._read_env_secret(api_key_env)

        api_secret_env = options.get("api_secret_env")
        if api_secret_env:
            updates["api_secret"] = self._read_env_secret(api_secret_env)

        if options.get("prompt_for_secrets"):
            updates["api_key"] = getpass.getpass("GoDaddy API key: ")
            updates["api_secret"] = getpass.getpass("GoDaddy API secret: ")

        if options.get("api_key") is not None:
            updates["api_key"] = options["api_key"]
            self.stdout.write(
                self.style.WARNING(
                    "Passing secrets via --api-key may expose them to process listings; "
                    "prefer --api-key-env or --prompt-for-secrets."
                )
            )

        if options.get("api_secret") is not None:
            updates["api_secret"] = options["api_secret"]
            self.stdout.write(
                self.style.WARNING(
                    "Passing secrets via --api-secret may expose them to process listings; "
                    "prefer --api-secret-env or --prompt-for-secrets."
                )
            )

    @staticmethod
    def _read_env_secret(env_var_name: str) -> str:
        """Read a secret from an environment variable."""

        value = os.environ.get(env_var_name)
        if value is None:
            raise CommandError(f"Environment variable '{env_var_name}' is not set.")
        return value

    def _update_credential(self, credential_id: int, updates: dict) -> None:
        """Apply field updates to a GoDaddy credential."""

        try:
            credential = DNSProviderCredential.objects.get(
                pk=credential_id,
                provider=DNSProviderCredential.Provider.GODADDY,
            )
        except DNSProviderCredential.DoesNotExist:
            raise CommandError(f"GoDaddy credential with id={credential_id} does not exist.")

        if not updates:
            self.stdout.write(
                self.style.WARNING(
                    "No edit flags were provided; listing credentials without changes."
                )
            )
            return

        for field_name, value in updates.items():
            setattr(credential, field_name, value)

        update_fields = list(updates.keys())
        credential.save(update_fields=update_fields)
        self.stdout.write(
            self.style.SUCCESS(f"Updated GoDaddy credential id={credential_id}.")
        )

    def _render_listing(self) -> None:
        """Print all stored GoDaddy credentials in a readable table."""

        credentials = DNSProviderCredential.objects.filter(
            provider=DNSProviderCredential.Provider.GODADDY
        ).order_by("id")

        if not credentials.exists():
            self.stdout.write("No GoDaddy credentials configured.")
            return

        self.stdout.write("GoDaddy credentials:")
        for credential in credentials:
            self.stdout.write(
                " - id={id} enabled={enabled} sandbox={sandbox} default_domain={domain} "
                "customer_id={customer} api_key={api_key} api_secret={api_secret}".format(
                    id=credential.id,
                    enabled=credential.is_enabled,
                    sandbox=credential.use_sandbox,
                    domain=credential.default_domain or "-",
                    customer=credential.customer_id or "-",
                    api_key=self._mask_secret(credential.api_key),
                    api_secret=self._mask_secret(credential.api_secret),
                )
            )

    @staticmethod
    def _mask_secret(value: str) -> str:
        """Return a masked representation suitable for terminal output."""

        cleaned = (value or "").strip()
        if not cleaned:
            return "-"
        if len(cleaned) <= 2:
            return "*" * len(cleaned)
        if len(cleaned) <= 8:
            return f"{cleaned[0]}{'*' * (len(cleaned) - 2)}{cleaned[-1]}"
        return f"{cleaned[:2]}{'*' * (len(cleaned) - 4)}{cleaned[-2:]}"
