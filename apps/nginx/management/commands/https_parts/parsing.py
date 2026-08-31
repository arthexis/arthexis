"""CLI parsing helpers for the HTTPS command."""

from __future__ import annotations

import ipaddress
import re

from django.core.management.base import CommandError

from config.settings_helpers import normalize_site_host

_DOMAIN_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,63}$")


def _is_valid_public_hostname(hostname: str) -> bool:
    """Return ``True`` when hostname only contains valid DNS label characters."""

    if len(hostname) > 253:
        return False

    labels = hostname.split(".")
    for label in labels:
        if not label or label.startswith("-") or label.endswith("-"):
            return False
        if not _DOMAIN_LABEL_PATTERN.fullmatch(label):
            return False

    return True


def _parse_site_domain(candidate: str | None) -> str | None:
    """Return a normalized host parsed from ``--site`` input."""

    normalized = normalize_site_host(candidate or "")
    if not normalized:
        raise CommandError("--site must include a valid hostname or URL.")

    if normalized == "localhost":
        raise CommandError("--site requires a public host. Use --local for local development.")

    if normalized.startswith("-"):
        raise CommandError("--site must include a valid hostname or URL.")

    ip_candidate = normalized
    if ip_candidate.startswith("[") and ip_candidate.endswith("]"):
        ip_candidate = ip_candidate[1:-1]

    try:
        parsed_ip = ipaddress.ip_address(ip_candidate)
    except ValueError:
        parsed_ip = None

    if parsed_ip is None and not _is_valid_public_hostname(normalized):
        raise CommandError("--site must include a valid hostname or URL.")

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
