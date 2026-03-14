from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.nginx.management.commands.https_parts import HttpsProvisioningService


class Command(BaseCommand):
    help = "Manage HTTPS certificates and nginx configuration."

    def add_arguments(self, parser):
        """Register HTTPS command flags and positional-domain compatibility behavior.

        Parameters:
            parser: Django command parser receiving action, certificate source,
                migration, execution, and compatibility arguments.

        Return:
            None. Arguments are registered directly on ``parser``.

        Raises:
            CommandError: Downstream validation in the service raises command
                errors for incompatible combinations such as conflicting domain
                selectors or ``--local`` with a public-domain selector.

        Notes:
            ``action_group`` ensures only one of ``--enable``, ``--disable``,
            ``--renew``, or ``--validate`` is explicit per invocation.
            ``cert_group`` enforces one certificate-source selector among
            ``--local``, ``--certbot``, and ``--godaddy``.
            ``sandbox_group`` controls GoDaddy DNS API environment overrides.
            The positional ``domain`` argument is a backward-compatible shortcut
            treated like a public-domain selector when explicit selectors are
            omitted; explicit ``--certbot``, ``--godaddy``, and ``--site`` take
            precedence. Without selectors, command behavior defaults to local
            certificate flows unless an explicit public-domain selector is set.
        """

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
        action_group.add_argument(
            "--validate",
            action="store_true",
            help="Validate tracked certificates and show detailed status output.",
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
            "--migrate-from",
            metavar="HOST_OR_URL",
            help=(
                "Optional existing host or URL to migrate to the target --site/--certbot/--godaddy domain. "
                "This updates local Site/Node links and reuses the prior nginx site configuration when possible."
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
        parser.add_argument(
            "domain",
            nargs="?",
            help=(
                "Optional target domain filter for --renew/--validate compatibility "
                "(same as passing --certbot/--godaddy domain)."
            ),
        )

    def handle(self, *args, **options):
        """Dispatch HTTPS actions through a dedicated provisioning service."""

        HttpsProvisioningService(command=self).handle(options)
