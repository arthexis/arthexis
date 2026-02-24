"""Site configuration model for managed nginx setup."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core import validators
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.nginx import services
from apps.nginx.config_utils import default_certificate_domain_from_settings
from apps.nginx.discovery import (
    _discover_site_config_paths,
    _format_local_load_error,
    _read_int_lock,
    _read_lock,
    _resolve_site_destination,
)
from apps.nginx.parsers import (
    _detect_external_websockets,
    _detect_https_enabled,
    _detect_ipv6_enabled,
    _extract_proxy_port,
    _extract_server_name,
    parse_subdomain_prefixes,
)


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

    def get_subdomain_prefixes(self) -> list[str]:
        return parse_subdomain_prefixes(self.managed_subdomains, strict=False)

    def clean(self):
        super().clean()
        parse_subdomain_prefixes(self.managed_subdomains)

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
                subdomain_prefixes=self.get_subdomain_prefixes(),
                reload=reload,
            )

        self.last_applied_at = timezone.now()
        if result.validated:
            self.last_validated_at = timezone.now()
        self.last_message = result.message
        self.save(update_fields=["last_applied_at", "last_validated_at", "last_message"])
        return result

    def validate_only(self) -> services.ApplyResult:
        """Validate current nginx configuration without full render/apply."""

        result = services.restart_nginx()
        self.last_validated_at = timezone.now()
        self.last_message = result.message
        self.save(update_fields=["last_validated_at", "last_message"])
        return result

    @classmethod
    def get_default(cls) -> "SiteConfiguration":
        """Get or create the default configuration using lock-file discovery defaults."""

        lock_dir = Path(settings.BASE_DIR) / ".locks"
        mode = _read_lock(lock_dir, "nginx_mode.lck", "internal").lower()
        if mode not in {"internal", "public"}:
            mode = "internal"

        defaults = {
            "mode": mode,
            "role": _read_lock(lock_dir, "role.lck", "Terminal"),
            "port": _read_int_lock(lock_dir, "backend_port.lck", 8888),
        }
        default_name = default_certificate_domain_from_settings(settings)

        if default_name != "default":
            cls.objects.get_or_create(name="default", defaults=defaults)

        obj, _created = cls.objects.get_or_create(name=default_name, defaults=defaults)
        return obj

    @classmethod
    def load_local_configurations(
        cls,
        *,
        base_dir: Path | None = None,
        site_path: Path | None = None,
    ) -> dict[str, object]:
        """Load nginx configuration files from disk into persisted SiteConfiguration rows."""

        resolved_base = Path(base_dir) if base_dir is not None else Path(settings.BASE_DIR)
        lock_dir = resolved_base / ".locks"
        mode = _read_lock(lock_dir, "nginx_mode.lck", "internal").lower()
        if mode not in {"internal", "public"}:
            mode = "internal"
        role = _read_lock(lock_dir, "role.lck", "Terminal")
        default_port = _read_int_lock(lock_dir, "backend_port.lck", 8888)
        site_entries_path = cls._meta.get_field("site_entries_path").get_default()
        resolved_site_path = site_path or Path(
            getattr(settings, "NGINX_SITE_PATH", "") or "/etc/nginx/sites-enabled/arthexis.conf"
        )
        candidate_paths = _discover_site_config_paths(resolved_site_path)

        results: dict[str, object] = {"created": 0, "updated": 0, "errors": []}
        seen_names: set[str] = set()

        if not candidate_paths:
            results["errors"].append("No local nginx site configurations found.")
            return results

        for path in candidate_paths:
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as exc:
                results["errors"].append(_format_local_load_error(path, exc))
                continue

            name = _extract_server_name(content) or default_certificate_domain_from_settings(settings)
            if not name:
                name = path.stem

            base_name = name
            counter = 2
            while name in seen_names:
                name = f"{base_name}-{counter}"
                counter += 1
            seen_names.add(name)

            port = _extract_proxy_port(content) or default_port
            protocol = "https" if _detect_https_enabled(content) else "http"
            include_ipv6 = _detect_ipv6_enabled(content)
            external_websockets = _detect_external_websockets(content)

            defaults = {
                "enabled": True,
                "mode": mode,
                "protocol": protocol,
                "role": role,
                "port": port,
                "include_ipv6": include_ipv6,
                "external_websockets": external_websockets,
                "expected_path": str(path),
                "site_entries_path": site_entries_path,
                "site_destination": _resolve_site_destination(),
            }

            _obj, created = cls.objects.update_or_create(name=name, defaults=defaults)
            if created:
                results["created"] += 1
            else:
                results["updated"] += 1

        return results
