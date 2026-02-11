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
NGINX_PROXY_PASS_RE = re.compile(r"proxy_pass\s+https?://[^:]+:(\d+)")
NGINX_SSL_LISTEN_RE = re.compile(r"listen\s+[^;]*\b443\b[^;]*ssl", re.IGNORECASE)
NGINX_SSL_CERTIFICATE_RE = re.compile(r"ssl_certificate\s+[^;]+;", re.IGNORECASE)
NGINX_IPV6_LISTEN_RE = re.compile(r"listen\s+\[::\][^;]*;", re.IGNORECASE)
NGINX_SERVER_NAME_RE = re.compile(r"server_name\s+([^;]+);")
NGINX_EXTERNAL_WEBSOCKETS_TOKEN = "proxy_set_header Connection $connection_upgrade;"
DEFAULT_SITE_DESTINATION = "/etc/nginx/sites-enabled/arthexis-sites.conf"


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


def _extract_proxy_port(content: str) -> int | None:
    for match in NGINX_PROXY_PASS_RE.findall(content):
        try:
            port = int(match)
        except ValueError:
            continue
        if 1 <= port <= 65535:
            return port
    return None


def _extract_server_name(content: str) -> str:
    for match in NGINX_SERVER_NAME_RE.findall(content):
        for token in match.split():
            token = token.strip()
            if not token or token == "_" or "*" in token or token.startswith("."):
                continue
            return token
    return ""


def _detect_https_enabled(content: str) -> bool:
    if NGINX_SSL_LISTEN_RE.search(content):
        return True
    return bool(NGINX_SSL_CERTIFICATE_RE.search(content))


def _detect_ipv6_enabled(content: str) -> bool:
    return bool(NGINX_IPV6_LISTEN_RE.search(content))


def _detect_external_websockets(content: str) -> bool:
    return NGINX_EXTERNAL_WEBSOCKETS_TOKEN in content


def _discover_site_config_paths(site_path: Path | None) -> list[Path]:
    candidates: set[Path] = set()
    if site_path and site_path.exists():
        if site_path.is_dir():
            candidates.update(site_path.glob("arthexis*.conf"))
        else:
            candidates.add(site_path)

    enabled_candidates = [
        path
        for path in services.SITES_ENABLED_DIR.glob("arthexis*.conf")
        if not path.name.endswith("-sites.conf")
    ]
    candidates.update(enabled_candidates)
    if not enabled_candidates:
        available_candidates = [
            path
            for path in services.SITES_AVAILABLE_DIR.glob("arthexis*.conf")
            if not path.name.endswith("-sites.conf")
        ]
        candidates.update(available_candidates)
    return sorted(candidates)


def _resolve_site_destination() -> str:
    candidates = [
        services.SITES_ENABLED_DIR / "arthexis-sites.conf",
        services.SITES_AVAILABLE_DIR / "arthexis-sites.conf",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return DEFAULT_SITE_DESTINATION


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
    TRANSPORT_CHOICES = (
        ("nginx", "nginx"),
        ("daphne", "daphne"),
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
    transport = models.CharField(
        max_length=16,
        choices=TRANSPORT_CHOICES,
        default="nginx",
        help_text=_("HTTPS orchestration transport backend."),
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
    tls_certificate_path = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text=_("Resolved TLS certificate path for direct Daphne TLS mode."),
    )
    tls_certificate_key_path = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text=_("Resolved TLS private key path for direct Daphne TLS mode."),
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

    @property
    def is_direct_tls_enabled(self) -> bool:
        """Return ``True`` when HTTPS is served directly by Daphne."""

        return (
            self.enabled
            and self.protocol == "https"
            and self.transport == "daphne"
            and bool(self.tls_certificate_path)
            and bool(self.tls_certificate_key_path)
        )

    def resolve_tls_paths(self) -> tuple[Path | None, Path | None]:
        """Resolve certificate and key paths from config overrides or certificate model."""

        cert_path = self.tls_certificate_path or ""
        key_path = self.tls_certificate_key_path or ""
        if self.certificate_id:
            certificate_path, certificate_key_path = self.certificate.resolve_material_paths()
            cert_path = cert_path or (str(certificate_path) if certificate_path else "")
            key_path = key_path or (str(certificate_key_path) if certificate_key_path else "")
        cert_file = Path(cert_path) if cert_path else None
        key_file = Path(key_path) if key_path else None
        return cert_file, key_file

    def sync_tls_paths_from_certificate(self) -> None:
        """Persist TLS material paths from the linked certificate when available."""

        if not self.certificate_id:
            return
        self.tls_certificate_path = self.certificate.certificate_path or ""
        self.tls_certificate_key_path = self.certificate.certificate_key_path or ""

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

    @classmethod
    def load_local_configurations(
        cls,
        *,
        base_dir: Path | None = None,
        site_path: Path | None = None,
    ) -> dict[str, object]:
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
                results["errors"].append(f"{path}: {exc}")
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

            obj, created = cls.objects.update_or_create(name=name, defaults=defaults)
            if created:
                results["created"] += 1
            else:
                results["updated"] += 1

        return results
