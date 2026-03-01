"""Site configuration lookup and apply helpers for HTTPS provisioning."""

from __future__ import annotations

import sys

from django.core.management.base import CommandError

from apps.nginx.management.commands.https_parts.constants import (
    NGINX_CONFIGURE_REMEDIATION_TEMPLATE,
)
from apps.nginx.models import SiteConfiguration
from apps.nginx.services import NginxUnavailableError, ValidationError


def _get_existing_config(domain: str) -> SiteConfiguration | None:
    """Return an existing site configuration for ``domain`` if present."""

    name = "localhost" if domain == "localhost" else domain
    return SiteConfiguration.objects.filter(name=name).first()


def _get_or_create_config(domain: str, *, protocol: str) -> SiteConfiguration:
    """Return a site configuration for ``domain`` and enforce enabled/protocol fields."""

    defaults_source = SiteConfiguration.get_default()
    name = "localhost" if domain == "localhost" else domain
    config, created = SiteConfiguration.objects.get_or_create(name=name)
    if created:
        config.enabled = True
        config.protocol = protocol
        config.mode = defaults_source.mode
        config.role = defaults_source.role
        config.port = defaults_source.port
        config.include_ipv6 = defaults_source.include_ipv6
        config.external_websockets = defaults_source.external_websockets
        config.site_entries_path = defaults_source.site_entries_path
        config.site_destination = defaults_source.site_destination
        config.expected_path = defaults_source.expected_path
        config.save()
    else:
        if config.protocol != protocol or not config.enabled:
            config.protocol = protocol
            config.enabled = True
            config.save(update_fields=["protocol", "enabled"])
    return config


def _apply_config(service, config: SiteConfiguration, *, reload: bool) -> None:
    """Apply nginx configuration and surface remediation guidance on failures."""

    try:
        result = config.apply(reload=reload)
    except (NginxUnavailableError, ValidationError) as exc:
        raise CommandError(
            f"{exc}\n" + NGINX_CONFIGURE_REMEDIATION_TEMPLATE.format(command=sys.argv[0])
        ) from exc

    service.stdout.write(service.style.SUCCESS(result.message))
    if not result.validated:
        service.stdout.write("nginx configuration applied but validation was skipped or failed.")
    if not result.reloaded:
        service.stdout.write(
            "nginx reload/start did not complete automatically; check the service status."
        )
