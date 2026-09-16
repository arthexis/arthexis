"""Legacy nginx configuration rows retained during app retirement.

The operational nginx implementation now lives outside ArtHexis. This model is
kept temporarily so existing databases and migration history remain loadable
until the model/app retirement phase.
"""

from __future__ import annotations

from django.core import validators
from django.db import models
from django.utils.translation import gettext_lazy as _


class SiteConfiguration(models.Model):
    """Persist the legacy nginx configuration schema during retirement."""

    MODE_CHOICES = (
        ("internal", "Internal"),
        ("public", "Public"),
    )
    PROTOCOL_CHOICES = (
        ("http", "HTTP"),
        ("https", "HTTPS"),
    )

    name = models.CharField(max_length=64, unique=True, default="default")
    enabled = models.BooleanField(default=True)
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default="internal")
    protocol = models.CharField(
        max_length=5,
        choices=PROTOCOL_CHOICES,
        default="http",
        help_text=_("Include HTTPS listeners when set to HTTPS."),
    )
    role = models.CharField(max_length=64, default="Terminal")
    port = models.PositiveIntegerField(
        default=8888,
        validators=[
            validators.MinValueValidator(1),
            validators.MaxValueValidator(65535),
        ],
    )
    external_websockets = models.BooleanField(
        default=True,
        help_text=_("Enable websocket proxy directives for external EVCS traffic."),
    )
    managed_subdomains = models.TextField(
        blank=True,
        default="",
        help_text=_(
            "Comma-separated subdomain prefixes to include for each managed site "
            "(for example: api, admin, status)."
        ),
    )
    include_ipv6 = models.BooleanField(default=False)
    expected_path = models.CharField(
        max_length=255,
        default="/etc/nginx/sites-enabled/arthexis.conf",
        help_text=_(
            "Filesystem path where the managed nginx configuration is applied."
        ),
    )
    site_entries_path = models.CharField(
        max_length=255,
        default="scripts/generated/nginx-sites.json",
        help_text=_(
            "Staged site definitions to include when rendering managed servers."
        ),
    )
    site_destination = models.CharField(
        max_length=255,
        default="/etc/nginx/sites-enabled/arthexis-sites.conf",
        help_text=_("Destination for the rendered managed site server blocks."),
    )
    last_applied_at = models.DateTimeField(null=True, blank=True)
    last_validated_at = models.DateTimeField(null=True, blank=True)
    last_message = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = _("Site configuration")
        verbose_name_plural = _("Site Server Configs")

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"NGINX site {self.name}" if self.name else "NGINX site configuration"
