"""Site configuration lookup and apply helpers for HTTPS provisioning."""

from __future__ import annotations

import sys

from django.conf import settings
from django.core.management.base import CommandError
from django.db.models import F

from apps.nginx.management.commands.https_parts.constants import (
    NGINX_CONFIGURE_REMEDIATION_TEMPLATE,
)
from apps.nginx.models import SiteConfiguration
from apps.nginx.services import NginxUnavailableError, ValidationError


_RUNTIME_INHERITED_FIELDS = (
    "mode",
    "role",
    "port",
    "include_ipv6",
    "external_websockets",
    "managed_subdomains",
)


def _get_existing_config(domain: str) -> SiteConfiguration | None:
    """Return an existing site configuration for ``domain`` if present."""

    name = "localhost" if domain == "localhost" else domain
    return SiteConfiguration.objects.filter(name=name).first()


def _default_config_defaults() -> dict[str, object]:
    """Return safe default managed-site paths for new configs."""

    return {
        "site_entries_path": (
            SiteConfiguration._meta.get_field("site_entries_path").get_default()
        ),
        "site_destination": (
            SiteConfiguration._meta.get_field("site_destination").get_default()
        ),
    }


def _default_expected_path(defaults_source: SiteConfiguration) -> str:
    """Return the primary nginx config path for new configs."""

    meta = SiteConfiguration._meta
    site_entries_default = meta.get_field("site_entries_path").get_default()
    site_destination_default = meta.get_field("site_destination").get_default()
    configured_default = (
        getattr(settings, "NGINX_SITE_PATH", "")
        or meta.get_field("expected_path").get_default()
    )

    if (
        defaults_source.site_entries_path == site_entries_default
        and defaults_source.site_destination == site_destination_default
    ):
        return defaults_source.expected_path

    return configured_default


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
        defaults = _default_config_defaults()
        defaults["expected_path"] = _default_expected_path(defaults_source)
        for field_name in _RUNTIME_INHERITED_FIELDS:
            defaults[field_name] = getattr(defaults_source, field_name)

        config = SiteConfiguration.objects.create(
            name=name,
            enabled=True,
            protocol=protocol,
            **defaults,
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
            "nginx applied the configuration, but validation was skipped or failed."
        )
    if not result.reloaded:
        service.stdout.write(
            "nginx was not reloaded automatically; check the service status."
        )
