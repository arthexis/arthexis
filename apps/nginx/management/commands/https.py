"""HTTPS certificate and nginx configuration management command."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.nginx.management.commands.https_parts import HttpsProvisioningService


class Command(BaseCommand):
    """Entry-point command that delegates behavior to the HTTPS provisioning service."""

    help = "Manage HTTPS certificates and nginx configuration."

    def add_arguments(self, parser):
        """Register CLI options for HTTPS enable/disable/report/renew actions."""

        action_group = parser.add_mutually_exclusive_group()
        action_group.add_argument(
            "--enable",
            action="store_true",
            help="Enable HTTPS and apply nginx configuration.",
        )
        action_group.add_argument(
            "--disable",
            action="store_true",
            help="Disable HTTPS and apply nginx configuration.",
        )
        action_group.add_argument(
            "--renew",
            action="store_true",
            help="Renew all due HTTPS certificates.",
        )

        cert_group = parser.add_mutually_exclusive_group()
        cert_group.add_argument(
            "--local",
            action="store_true",
            help="Use a self-signed localhost certificate (default).",
        )
        cert_group.add_argument(
            "--certbot",
            metavar="DOMAIN",
            help="Use certbot for the specified domain.",
        )
        cert_group.add_argument(
            "--godaddy",
            metavar="DOMAIN",
            help="Use certbot DNS-01 with GoDaddy for the specified domain.",
        )

        sandbox_group = parser.add_mutually_exclusive_group()
        sandbox_group.add_argument(
            "--sandbox",
            action="store_true",
            help="Force GoDaddy DNS requests to use the OTE sandbox API for this run.",
        )
        sandbox_group.add_argument(
            "--no-sandbox",
            action="store_true",
            help="Force GoDaddy DNS requests to use the production API for this run.",
        )

        parser.add_argument(
            "--site",
            metavar="HOST_OR_URL",
            help=(
                "Target host or URL to enable (for example, porsche.example.com or "
                "wss://porsche.example.com/)."
            ),
        )
        parser.add_argument(
            "--no-reload",
            action="store_true",
            help="Skip nginx reload/restart after applying changes.",
        )
        parser.add_argument(
            "--no-sudo",
            action="store_true",
            help="Run certificate provisioning without sudo.",
        )
        parser.add_argument(
            "--force-renewal",
            action="store_true",
            help="Force certbot to issue a fresh certificate even if one already exists.",
        )
        parser.add_argument(
            "--warn-days",
            type=int,
            default=14,
            help="Warn when certificate expiration is within this many days (default: 14).",
        )

    def handle(self, *args, **options):
        """Delegate command execution to the HTTPS provisioning service."""

        HttpsProvisioningService(self).run(options)
