from __future__ import annotations

import os
import sys
from getpass import getpass

import requests
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
            choices=("add", "remove", "list", "setup", "verify"),
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
        parser.add_argument("--customer-id", help="Optional GoDaddy customer ID.")
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Verify GoDaddy credentials against GoDaddy API.",
        )
        parser.add_argument(
            "--key",
            help="Credential selector for verify action (credential ID or API key).",
        )
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
        if options.get("verify") and action == "list":
            self._handle_verify(options)
            return
        if action == "add":
            self._handle_add(options)
            return
        if action == "remove":
            self._handle_remove(options)
            return
        if action == "setup":
            self._handle_setup(options)
            return
        if action == "verify":
            self._handle_verify(options)
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
        """Document manual DNS setup steps instead of auto-configuring credentials."""

        raise CommandError(
            "Automated GoDaddy DNS setup was removed. Configure DNS records manually in GoDaddy DNS Manager, "
            "then use './command.sh nginx_configure' (or HTTPS enable with --certbot/--site) to apply nginx config."
        )

    def _resolve_credential_selector(
        self, options: dict[str, object]
    ) -> DNSProviderCredential:
        """Resolve selected GoDaddy credential for verify action."""

        raw_key = str(options.get("key") or "").strip()
        queryset = DNSProviderCredential.objects.filter(
            provider=DNSProviderCredential.Provider.GODADDY,
            is_enabled=True,
        ).order_by("pk")
        if not raw_key:
            credential = queryset.first()
            if credential is None:
                raise CommandError(
                    "No enabled GoDaddy credential was found. Configure one with './command.sh godaddy add ...'."
                )
            return credential

        credential = None
        if raw_key.isdigit():
            credential = queryset.filter(pk=int(raw_key)).first()
        if credential is None:
            credential = queryset.filter(api_key=raw_key).first()
        if credential is None:
            raise CommandError(
                f"GoDaddy credential '{raw_key}' was not found or is disabled."
            )
        return credential

    def _handle_verify(self, options: dict[str, object]) -> None:
        """Verify selected GoDaddy credentials by calling GoDaddy API."""

        credential = self._resolve_credential_selector(options)
        domain = credential.get_default_domain()
        base_url = credential.get_base_url()
        if domain:
            url = f"{base_url}/v1/domains/{domain}"
        else:
            url = f"{base_url}/v1/domains?limit=1"

        headers = {
            "Authorization": credential.get_auth_header(),
            "Accept": "application/json",
        }
        customer_id = credential.get_customer_id()
        if customer_id:
            headers["X-Shopper-Id"] = customer_id

        try:
            response = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as exc:
            raise CommandError(f"GoDaddy credential verify failed: {exc}") from exc
        if response.status_code >= 400:
            error_msg = response.text
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                message = payload.get("message")
                code = payload.get("code")
                if isinstance(message, str) and message.strip():
                    error_msg = message.strip()
                elif isinstance(code, str) and code.strip():
                    error_msg = code.strip()
            raise CommandError(
                "GoDaddy credential verify failed: "
                f"{response.status_code} {error_msg[:200]}"
            )
        target = domain or "<domain-list>"
        self.stdout.write(
            self.style.SUCCESS(
                f"GoDaddy credential #{credential.pk} verified for {target}."
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
