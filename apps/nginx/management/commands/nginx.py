from __future__ import annotations

import ipaddress
import socket
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.nginx.models import SiteConfiguration
from apps.nginx.services import ApplyResult, NginxUnavailableError, ValidationError


def write_apply_status(service, result: ApplyResult) -> None:
    """Write consistent nginx apply status messages to a command's output streams.

    Parameters:
        service: Management command instance providing ``stdout`` and ``style``.
        result: Outcome returned from ``SiteConfiguration.apply()``.

    Returns:
        None.
    """

    service.stdout.write(service.style.SUCCESS(result.message))
    if not result.validated:
        service.stdout.write(
            "nginx applied the configuration, but validation was skipped or failed."
        )
    if not result.reloaded:
        service.stdout.write(
            "nginx was not reloaded automatically; check the service status."
        )


class ConfigureMixin:
    """Reusable nginx configuration workflow for Django management commands.

    The mixin registers ``--configure``-related arguments, updates the default
    ``SiteConfiguration``, and applies or removes the managed nginx config,
    including validation and reload messaging. Mix into a ``BaseCommand``
    subclass that provides ``stdout`` and ``style`` attributes.
    """

    def add_configure_arguments(self, parser) -> None:
        """Register arguments used to apply or remove managed nginx configuration."""

        parser.add_argument("--mode", default=None, help="nginx mode (internal or public)")
        parser.add_argument("--port", type=int, default=None, help="Application port proxied by nginx")
        parser.add_argument("--role", default=None, help="Role label to persist alongside the configuration")
        parser.add_argument("--ip6", action="store_true", help="Include IPv6 listeners in the rendered configuration")
        parser.add_argument("--remove", action="store_true", help="Remove nginx configuration instead of applying it")
        parser.add_argument("--no-reload", action="store_true", help="Skip nginx reload/restart after applying changes")
        parser.add_argument(
            "--static-ip",
            default=None,
            help=(
                "Override detected public address with a known static public IP. "
                "Required when this host only has private/local interface addresses."
            ),
        )
        parser.add_argument(
            "--sites-config",
            default=None,
            help="Optional override for the staged site configuration JSON.",
        )
        parser.add_argument(
            "--sites-destination",
            default=None,
            help="Optional override for the managed site destination path.",
        )

    def run_configure(self, options: dict[str, object]) -> None:
        """Apply or remove the managed nginx configuration using the provided options.

        Parameters:
            options: Parsed Django command options containing ``mode``, ``port``,
                ``role``, ``ip6``, ``sites_config``, ``sites_destination``,
                ``remove``, and ``no_reload`` keys.

        Returns:
            None.

        Raises:
            CommandError: Raised when nginx is unavailable or configuration
                validation fails while applying the managed config.
        """

        static_ip = self._parse_static_ip(str(options.get("static_ip") or "").strip())
        if static_ip is None:
            public_ips = self._detect_public_ips()
            if not public_ips:
                raise CommandError(
                    "No public/static IP was detected on this host. "
                    "Aborting nginx configuration. Provide --static-ip <PUBLIC_IP> "
                    "or assign a public Elastic IP / Load Balancer endpoint first."
                )

        config = SiteConfiguration.get_default()

        if options["mode"]:
            config.mode = str(options["mode"]).lower()
        if options["port"]:
            config.port = options["port"]
        if options["role"]:
            config.role = options["role"]
        if options["ip6"]:
            config.include_ipv6 = True
        if options["sites_config"]:
            config.site_entries_path = options["sites_config"]
        if options["sites_destination"]:
            config.site_destination = options["sites_destination"]

        config.enabled = not options["remove"]
        config.save()

        reload = not options["no_reload"]

        try:
            result = config.apply(reload=reload, remove=options["remove"])
        except NginxUnavailableError as exc:  # pragma: no cover - requires system nginx
            raise CommandError(str(exc)) from exc
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        write_apply_status(self, result)

        if options["sites_config"]:
            self.stdout.write(f"Managed site definitions read from {Path(config.site_entries_path).resolve()}")
        if options["sites_destination"]:
            self.stdout.write(f"Managed sites written to {config.site_destination}")

    def _parse_static_ip(self, value: str) -> str | None:
        """Validate optional ``--static-ip`` input as a public IP address."""

        if not value:
            return None
        try:
            parsed = ipaddress.ip_address(value)
        except ValueError as exc:
            raise CommandError(f"--static-ip must be a valid IPv4 or IPv6 address: {value}") from exc
        if parsed.is_private or parsed.is_loopback or parsed.is_link_local or parsed.is_multicast:
            raise CommandError(f"--static-ip must be public-routable: {value}")
        return value

    def _detect_public_ips(self) -> list[str]:
        """Return detected public interface IPs for this host."""

        detected: set[str] = set()
        candidates: set[str] = set()
        hostname = socket.gethostname()

        for lookup_name in (hostname, socket.getfqdn(), "localhost"):
            if not lookup_name:
                continue
            try:
                infos = socket.getaddrinfo(lookup_name, None, proto=socket.IPPROTO_TCP)
            except socket.gaierror:
                continue
            for info in infos:
                host = info[4][0]
                if host:
                    candidates.add(host)

        for candidate in candidates:
            try:
                parsed = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if parsed.is_private or parsed.is_loopback or parsed.is_link_local or parsed.is_multicast:
                continue
            detected.add(str(parsed))

        return sorted(detected)


class Command(ConfigureMixin, BaseCommand):
    """Consolidated management command for nginx operations on this node.

    The Django ``help`` attribute drives CLI output, while this class docstring
    documents the developer-facing behavior of the unified nginx command.
    """

    help = "Manage nginx operations for this node."  # noqa: A003 - django requires 'help'

    def add_arguments(self, parser):
        """Register top-level nginx actions and configuration flags."""

        parser.add_argument(
            "--configure",
            action="store_true",
            help="Apply or remove the managed nginx configuration.",
        )
        self.add_configure_arguments(parser)

    def handle(self, *args, **options):
        """Dispatch nginx actions from the consolidated command surface."""

        if options["configure"]:
            self.run_configure(options)
            return

        raise CommandError("No nginx action was selected. Use --configure.")
