from __future__ import annotations

import ipaddress
from pathlib import Path

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.certs.models import CertificateBase, CertbotCertificate, SelfSignedCertificate
from apps.nginx.config_utils import slugify
from apps.nginx.models import SiteConfiguration


class CertificateGenerationMixin:
    CERTIFICATE_TYPE_SELF_SIGNED = "self-signed"
    CERTIFICATE_TYPE_CERTBOT = "certbot"

    def generate_certificates_view(self, request):  # pragma: no cover - admin plumbing
        if not self.has_change_permission(request):
            raise PermissionDenied

        ids_param, _, queryset = self._get_selection_from_request(request)

        if request.method == "POST":
            certificate_type = self._normalize_certificate_type(
                request.POST.get("certificate_type")
            )
            self._generate_certificates(
                request, queryset, ids_param, certificate_type=certificate_type
            )
            redirect_url = reverse("admin:nginx_siteconfiguration_preview")
            if ids_param:
                redirect_url = f"{redirect_url}?ids={ids_param}"
            return self._http_redirect(redirect_url)

        return self._http_redirect(reverse("admin:nginx_siteconfiguration_changelist"))

    @staticmethod
    def _http_redirect(url):  # pragma: no cover - thin wrapper for easier testing
        from django.http import HttpResponseRedirect

        return HttpResponseRedirect(url)

    def _generate_certificates(
        self,
        request,
        queryset,
        ids_param: str = "",
        *,
        certificate_type: str = CERTIFICATE_TYPE_SELF_SIGNED,
    ):
        for config in queryset:
            if config.protocol != "https":
                config.protocol = "https"
                config.save(update_fields=["protocol"])
                self.message_user(
                    request,
                    _("%s: HTTPS enabled to allow certificate provisioning.") % config,
                    messages.INFO,
                )

            certificate: CertificateBase | None = config.certificate
            if certificate is None:
                certificate = self._create_certificate_for_config(
                    config, certificate_type=certificate_type
                )
                created_label = self._certificate_type_label(certificate_type)
                self.message_user(
                    request,
                    _("%(config)s: Created a %(type)s certificate for %(domain)s.")
                    % {
                        "config": config,
                        "type": created_label,
                        "domain": certificate.domain,
                    },
                    messages.INFO,
                )

            try:
                message = certificate.provision()
            except Exception as exc:  # pragma: no cover - admin plumbing
                self.message_user(request, f"{config}: {exc}", messages.ERROR)
            else:
                self.message_user(
                    request,
                    _("%s: %s") % (config, message),
                    messages.SUCCESS,
                )

    def _create_certificate_for_config(
        self, config: SiteConfiguration, *, certificate_type: str
    ) -> CertificateBase:
        if certificate_type == self.CERTIFICATE_TYPE_CERTBOT:
            return self._create_certbot_certificate_for_config(config)
        return self._create_self_signed_certificate_for_config(config)

    def _create_self_signed_certificate_for_config(self, config: SiteConfiguration) -> CertificateBase:
        domain = self._get_default_certificate_domain()
        slug = slugify(domain)
        base_path = Path(settings.BASE_DIR) / "scripts" / "generated" / "certificates" / slug
        defaults = {
            "domain": domain,
            "certificate_path": str(base_path / "fullchain.pem"),
            "certificate_key_path": str(base_path / "privkey.pem"),
        }

        certificate, created = SelfSignedCertificate.objects.get_or_create(
            name=f"{config.name or 'nginx-site'}-{slug}",
            defaults=defaults,
        )

        updated_fields: list[str] = []
        if not created:
            for field, value in defaults.items():
                if getattr(certificate, field) != value:
                    setattr(certificate, field, value)
                    updated_fields.append(field)
            if updated_fields:
                certificate.save(update_fields=updated_fields)

        if config.certificate_id != certificate.id:
            config.certificate = certificate
            config.save(update_fields=["certificate"])

        return certificate

    def _create_certbot_certificate_for_config(self, config: SiteConfiguration) -> CertificateBase:
        domain = self._get_default_certificate_domain()
        slug = slugify(domain)
        defaults = {
            "domain": domain,
            "certificate_path": f"/etc/letsencrypt/live/{domain}/fullchain.pem",
            "certificate_key_path": f"/etc/letsencrypt/live/{domain}/privkey.pem",
        }

        certificate, created = CertbotCertificate.objects.get_or_create(
            name=f"{config.name or 'nginx-site'}-{slug}-certbot",
            defaults=defaults,
        )

        updated_fields: list[str] = []
        if not created:
            for field, value in defaults.items():
                if getattr(certificate, field) != value:
                    setattr(certificate, field, value)
                    updated_fields.append(field)
            if updated_fields:
                certificate.save(update_fields=updated_fields)

        if config.certificate_id != certificate.id:
            config.certificate = certificate
            config.save(update_fields=["certificate"])

        return certificate

    def _find_missing_certificates(self, queryset):
        return [
            config
            for config in queryset
            if config.protocol == "https" and config.certificate is None
        ]

    def _get_default_certificate_domain(self) -> str:
        hosts = getattr(settings, "ALLOWED_HOSTS", []) or []
        candidates: list[str] = []
        for host in hosts:
            normalized = str(host or "").strip()
            if not normalized or normalized.startswith("."):
                continue
            if "/" in normalized:
                continue
            if normalized.startswith("[") and "]" in normalized:
                normalized = normalized.split("]", 1)[0].lstrip("[")
            elif ":" in normalized and normalized.count(":") == 1:
                normalized = normalized.rsplit(":", 1)[0]
            if not normalized:
                continue
            try:
                ipaddress.ip_address(normalized)
            except ValueError:
                candidates.append(normalized)
            else:
                continue

        for candidate in candidates:
            if "." in candidate:
                return candidate

        if candidates:
            return candidates[0]

        return "localhost"

    def _certificate_type_choices(self) -> tuple[tuple[str, str], ...]:
        return (
            (self.CERTIFICATE_TYPE_SELF_SIGNED, _("Self-signed")),
            (self.CERTIFICATE_TYPE_CERTBOT, _("Certbot")),
        )

    def _normalize_certificate_type(self, value: str | None) -> str:
        if value == self.CERTIFICATE_TYPE_CERTBOT:
            return value
        return self.CERTIFICATE_TYPE_SELF_SIGNED

    def _certificate_type_label(self, value: str) -> str:
        return dict(self._certificate_type_choices()).get(value, _("self-signed"))

    @property
    def default_certificate_type(self) -> str:
        return self.CERTIFICATE_TYPE_SELF_SIGNED

    @admin.action(description=_("Generate certificates"))
    def generate_certificates(self, request, queryset):
        self._generate_certificates(request, queryset)

