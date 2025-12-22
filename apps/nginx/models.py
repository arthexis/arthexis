from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core import validators
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.nginx import services


def _read_lock(lock_dir: Path, name: str, fallback: str) -> str:
    try:
        value = (lock_dir / name).read_text(encoding="utf-8").strip()
    except OSError:
        return fallback
    return value or fallback


def _read_int_lock(lock_dir: Path, name: str, fallback: int) -> int:
    value = _read_lock(lock_dir, name, str(fallback))
    try:
        parsed = int(value)
    except ValueError:
        return fallback
    if parsed < 1 or parsed > 65535:
        return fallback
    return parsed


class SiteConfiguration(models.Model):
    """Represents the desired nginx site configuration for this deployment."""

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
        validators=[validators.MinValueValidator(1), validators.MaxValueValidator(65535)],
    )
    certificate = models.ForeignKey(
        "certs.CertificateBase",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="nginx_configurations",
    )
    external_websockets = models.BooleanField(
        default=True,
        help_text=_("Enable websocket proxy directives for external EVCS traffic."),
    )
    include_ipv6 = models.BooleanField(default=False)
    expected_path = models.CharField(
        max_length=255,
        default="/etc/nginx/sites-enabled/arthexis.conf",
        help_text=_("Filesystem path where the managed nginx configuration is applied."),
    )
    site_entries_path = models.CharField(
        max_length=255,
        default="scripts/generated/nginx-sites.json",
        help_text=_("Staged site definitions to include when rendering managed servers."),
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

    @property
    def expected_destination(self) -> Path:
        return Path(self.expected_path)

    @property
    def staged_site_config(self) -> Path:
        return Path(settings.BASE_DIR) / self.site_entries_path

    @property
    def site_destination_path(self) -> Path:
        return Path(self.site_destination)

    def apply(self, *, reload: bool = True, remove: bool = False) -> services.ApplyResult:
        """Apply or remove the managed nginx configuration."""

        if remove:
            result = services.remove_nginx_configuration(reload=reload)
        else:
            result = services.apply_nginx_configuration(
                mode=self.mode,
                port=self.port,
                role=self.role,
                certificate=self.certificate,
                https_enabled=self.protocol == "https",
                include_ipv6=self.include_ipv6,
                external_websockets=self.external_websockets,
                destination=self.expected_destination,
                site_config_path=self.staged_site_config,
                site_destination=self.site_destination_path,
                reload=reload,
            )

        self.last_applied_at = timezone.now()
        if result.validated:
            self.last_validated_at = timezone.now()
        self.last_message = result.message
        self.save(update_fields=["last_applied_at", "last_validated_at", "last_message"])
        return result

    def validate_only(self) -> services.ApplyResult:
        result = services.restart_nginx()
        self.last_validated_at = timezone.now()
        self.last_message = result.message
        self.save(update_fields=["last_validated_at", "last_message"])
        return result

    @classmethod
    def get_default(cls) -> "SiteConfiguration":
        lock_dir = Path(settings.BASE_DIR) / ".locks"
        defaults = {
            "mode": _read_lock(lock_dir, "nginx_mode.lck", "internal").lower(),
            "role": _read_lock(lock_dir, "role.lck", "Terminal"),
            "port": _read_int_lock(lock_dir, "backend_port.lck", 8888),
        }
        obj, created = cls.objects.get_or_create(name="default", defaults=defaults)
        if created:
            return obj
        return obj
