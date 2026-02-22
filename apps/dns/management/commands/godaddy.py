from __future__ import annotations

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
            choices=("add", "remove", "list"),
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
        self._handle_list()

    def _handle_add(self, options: dict[str, object]) -> None:
        """Create a new GoDaddy DNS credential."""

        username = str(options.get("user") or "").strip()
        api_key = str(options.get("api_key") or "").strip()
        api_secret = str(options.get("api_secret") or "").strip()

        missing: list[str] = []
        if not username:
            missing.append("--user")
        if not api_key:
            missing.append("--api-key")
        if not api_secret:
            missing.append("--api-secret")
        if missing:
            raise CommandError(f"Missing required arguments for add: {', '.join(missing)}")

        user = self._resolve_user(username)

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
            return user_model._default_manager.get(username=username)
        except user_model.DoesNotExist as exc:
            raise CommandError(f"User '{username}' does not exist.") from exc

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

    def _handle_list(self) -> None:
        """Print GoDaddy DNS credentials."""

        credentials = DNSProviderCredential.objects.filter(
            provider=DNSProviderCredential.Provider.GODADDY,
        ).order_by("pk")
        if not credentials.exists():
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
