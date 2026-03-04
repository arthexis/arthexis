"""Site configuration lookup and apply helpers for HTTPS provisioning."""

from __future__ import annotations

import sys

from django.core.management.base import CommandError
from django.db.models import F

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

    name = "localhost" if domain == "localhost" else domain

    try:
        config = SiteConfiguration.objects.get(name=name)
        created = False
    except SiteConfiguration.DoesNotExist:
        defaults_source = (
            SiteConfiguration.objects.filter(enabled=True)
            .order_by(F("last_applied_at").desc(nulls_last=True), "-id")
            .first()
            or SiteConfiguration.get_default()
        )
        config = SiteConfiguration.objects.create(
            name=name,
            enabled=True,
            protocol=protocol,
            mode=defaults_source.mode,
            role=defaults_source.role,
            port=defaults_source.port,
            include_ipv6=defaults_source.include_ipv6,
            external_websockets=defaults_source.external_websockets,
            site_entries_path=defaults_source.site_entries_path,
            site_destination=defaults_source.site_destination,
            expected_path=defaults_source.expected_path,
        )
        created = True

    if not created and (config.protocol != protocol or not config.enabled):
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
            f"{exc}\n"
            + NGINX_CONFIGURE_REMEDIATION_TEMPLATE.format(command=sys.argv[0])
        ) from exc

    service.stdout.write(service.style.SUCCESS(result.message))
    if not result.validated:
        service.stdout.write(
            "nginx configuration applied but validation was skipped or failed."
        )
    if not result.reloaded:
        service.stdout.write(
            "nginx reload/start did not complete automatically; check the service status."
        )
