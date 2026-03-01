"""Input parsing helpers for the https management command."""

from __future__ import annotations

import ipaddress

from django.core.management.base import CommandError

from config.settings_helpers import normalize_site_host


def _parse_site_domain(candidate: str | None) -> str | None:
    """Return a normalized host parsed from ``--site`` input."""

    normalized = normalize_site_host(candidate or "")
    if not normalized:
        raise CommandError("--site must include a valid hostname or URL.")

    if normalized == "localhost":
        raise CommandError("--site requires a public host. Use --local for local development.")

    if normalized.startswith("-"):
        raise CommandError("--site must include a valid hostname or URL.")

    try:
        parsed_ip = ipaddress.ip_address(normalized)
    except ValueError:
        parsed_ip = None

    if parsed_ip is not None and parsed_ip.is_loopback:
        raise CommandError("--site requires a public host. Use --local for local development.")

    return normalized


def _parse_sandbox_override(options: dict[str, object]) -> bool | None:
    """Return a per-run GoDaddy sandbox override derived from CLI options."""

    if options["sandbox"]:
        return True
    if options["no_sandbox"]:
        return False
    return None
