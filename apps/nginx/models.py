from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.core import validators
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.nginx import services
from apps.nginx.config_utils import default_certificate_domain_from_settings


SUBDOMAIN_PREFIX_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def parse_subdomain_prefixes(raw: str, *, strict: bool = True) -> list[str]:
    prefixes: list[str] = []
    seen: set[str] = set()
    invalid: list[str] = []
    for token in re.split(r"[,\s]+", raw or ""):
        candidate = token.strip().lower()
        if not candidate:
            continue
        if "." in candidate or not SUBDOMAIN_PREFIX_RE.match(candidate):
            invalid.append(candidate)
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        prefixes.append(candidate)
    if invalid and strict:
        raise ValidationError(
            _("Invalid subdomain prefixes: %(invalid)s"),
            params={"invalid": ", ".join(sorted(invalid))},
        )
    return prefixes


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
    secondary_instance = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "Optional sibling installation folder to use as a failover target."
        ),
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

    def resolve_secondary_instance(self) -> services.SecondaryInstance | None:
        if not self.secondary_instance:
            return None
        return services.get_secondary_instance(self.secondary_instance)

    def get_subdomain_prefixes(self) -> list[str]:
        return parse_subdomain_prefixes(self.managed_subdomains, strict=False)

    def clean(self):
        super().clean()
        parse_subdomain_prefixes(self.managed_subdomains)
        if not self.secondary_instance:
            return
        try:
            self.resolve_secondary_instance()
        except services.SecondaryInstanceError as exc:
            raise ValidationError({"secondary_instance": str(exc)}) from exc

    def apply(self, *, reload: bool = True, remove: bool = False) -> services.ApplyResult:
        """Apply or remove the managed nginx configuration."""

        secondary_instance = None
        if not remove:
            try:
                secondary_instance = self.resolve_secondary_instance()
            except services.SecondaryInstanceError as exc:
                raise services.ValidationError(str(exc)) from exc

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
                subdomain_prefixes=self.get_subdomain_prefixes(),
                reload=reload,
                secondary_instance=secondary_instance,
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
        default_name = default_certificate_domain_from_settings(settings)

        if default_name != "default":
            cls.objects.get_or_create(name="default", defaults=defaults)

        obj, _created = cls.objects.get_or_create(name=default_name, defaults=defaults)
        return obj
