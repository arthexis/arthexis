from __future__ import annotations

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
        parser.add_argument("--api-key", help="Set API key.")
        parser.add_argument("--api-secret", help="Set API secret.")
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

        updates: dict[str, object] = {}
        text_fields = ("api_key", "api_secret", "customer_id", "default_domain")
        for field_name in text_fields:
            value = options.get(field_name)
            if value is not None:
                updates[field_name] = value

        if options.get("enable"):
            updates["is_enabled"] = True
        if options.get("disable"):
            updates["is_enabled"] = False
        if options.get("sandbox"):
            updates["use_sandbox"] = True
        if options.get("no_sandbox"):
            updates["use_sandbox"] = False

        return updates

    def _update_credential(self, credential_id: int, updates: dict) -> None:
        """Apply field updates to a GoDaddy credential."""

        credential = DNSProviderCredential.objects.filter(
            pk=credential_id,
            provider=DNSProviderCredential.Provider.GODADDY,
        ).first()
        if credential is None:
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
        if len(cleaned) <= 4:
            return "*" * len(cleaned)
        return f"{cleaned[:2]}{'*' * (len(cleaned) - 4)}{cleaned[-2:]}"
