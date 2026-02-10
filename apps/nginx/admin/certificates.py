from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.certs.services import CertbotError
from apps.dns.models import DNSProviderCredential
from apps.certs.models import CertificateBase, CertbotCertificate, SelfSignedCertificate
from apps.nginx.config_utils import default_certificate_domain_from_settings, slugify
from apps.nginx.models import SiteConfiguration


class CertificateGenerationMixin:
    CERTIFICATE_TYPE_SELF_SIGNED = "self-signed"
    CERTIFICATE_TYPE_CERTBOT = "certbot"
    VALIDATION_PROVIDER_GODADDY = "godaddy"

    def generate_certificates_view(self, request):  # pragma: no cover - admin plumbing
        if not self.has_change_permission(request):
            raise PermissionDenied

        ids_param, _, queryset = self._get_selection_from_request(request)

        if request.method == "POST":
            certificate_type = self._normalize_certificate_type(
                request.POST.get("certificate_type")
            )
            validation_provider = self._normalize_validation_provider(
                request.POST.get("validation_provider")
            )
            dns_propagation_seconds = self._normalize_dns_propagation_seconds(
                request.POST.get("dns_propagation_seconds")
            )
            self._generate_certificates(
                request,
                queryset,
                ids_param,
                certificate_type=certificate_type,
                validation_provider=validation_provider,
                dns_propagation_seconds=dns_propagation_seconds,
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
        validation_provider: str | None = None,
        dns_propagation_seconds: int = 60,
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

            provision_kwargs = self._get_validation_kwargs(
                config,
                certificate_type=certificate_type,
                validation_provider=validation_provider,
                dns_propagation_seconds=dns_propagation_seconds,
            )

            try:
                message = certificate.provision(**provision_kwargs)
            except CertbotError as exc:
                self._message_manual_dns_steps(request, config, str(exc))
                self.message_user(request, f"{config}: {exc}", messages.ERROR)
                continue
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
        domain = self._get_default_certificate_domain()
        params = self._certificate_parameters(certificate_type, domain)
        return self._upsert_certificate(
            config,
            model=params["model"],
            name=self._certificate_name(config, params["name_suffix"]),
            defaults=params["defaults"],
        )

    def _certificate_parameters(self, certificate_type: str, domain: str) -> dict:
        slug = slugify(domain)

        if certificate_type == self.CERTIFICATE_TYPE_CERTBOT:
            return {
                "model": CertbotCertificate,
                "name_suffix": f"{slug}-certbot",
                "defaults": {
                    "domain": domain,
                    "certificate_path": f"/etc/letsencrypt/live/{domain}/fullchain.pem",
                    "certificate_key_path": f"/etc/letsencrypt/live/{domain}/privkey.pem",
                },
            }

        base_path = (
            Path(settings.BASE_DIR) / "scripts" / "generated" / "certificates" / slug
        )
        return {
            "model": SelfSignedCertificate,
            "name_suffix": slug,
            "defaults": {
                "domain": domain,
                "certificate_path": str(base_path / "fullchain.pem"),
                "certificate_key_path": str(base_path / "privkey.pem"),
            },
        }

    def _upsert_certificate(self, config, *, model, name: str, defaults: dict) -> CertificateBase:
        certificate, created = model.objects.get_or_create(name=name, defaults=defaults)

        if not created:
            updated_fields = [
                field for field, value in defaults.items() if getattr(certificate, field) != value
            ]
            for field in updated_fields:
                setattr(certificate, field, defaults[field])
            if updated_fields:
                certificate.save(update_fields=updated_fields)

        if config.certificate_id != certificate.id:
            config.certificate = certificate
            config.save(update_fields=["certificate"])

        return certificate

    def _certificate_name(self, config: SiteConfiguration, suffix: str) -> str:
        return f"{config.name or 'nginx-site'}-{suffix}"

    def _find_missing_certificates(self, queryset):
        return [
            config
            for config in queryset
            if config.protocol == "https" and config.certificate is None
        ]

    def _get_default_certificate_domain(self) -> str:
        return default_certificate_domain_from_settings(settings)

    def _get_validation_kwargs(
        self,
        config: SiteConfiguration,
        *,
        certificate_type: str,
        validation_provider: str | None,
        dns_propagation_seconds: int,
    ) -> dict[str, object]:
        """Build provider-aware certbot provisioning arguments for admin actions."""
        if certificate_type != self.CERTIFICATE_TYPE_CERTBOT:
            return {}

        provider = (validation_provider or "").strip().lower()
        if provider != self.VALIDATION_PROVIDER_GODADDY:
            return {}

        credential = self._get_godaddy_credential_for_domain(config.certificate.domain)
        return {
            "validation_provider": provider,
            "dns_api_key": credential.resolve_sigils("api_key"),
            "dns_api_secret": credential.resolve_sigils("api_secret"),
            "dns_propagation_seconds": dns_propagation_seconds,
        }

    def _get_godaddy_credential_for_domain(self, domain: str) -> DNSProviderCredential:
        """Return the best enabled GoDaddy DNS credential for a certificate domain."""
        credentials = DNSProviderCredential.objects.filter(
            provider=DNSProviderCredential.Provider.GODADDY,
            is_enabled=True,
        )
        domain = domain.lower()
        for credential in credentials:
            default_domain = (credential.get_default_domain() or "").lower()
            if default_domain and (domain == default_domain or domain.endswith(f".{default_domain}")):
                return credential
        fallback = credentials.first()
        if fallback is None:
            raise CertbotError(
                "No enabled GoDaddy DNS credential found. Create one in Admin > DNS > DNS Credentials."
            )
        return fallback

    def _normalize_validation_provider(self, value: str | None) -> str | None:
        provider = (value or "").strip().lower()
        if provider == self.VALIDATION_PROVIDER_GODADDY:
            return provider
        return None

    def _normalize_dns_propagation_seconds(self, value: str | None) -> int:
        """Normalize DNS propagation wait seconds from form input."""
        try:
            parsed = int(value or 60)
        except (TypeError, ValueError):
            return 60
        return max(0, parsed)

    def _validation_provider_choices(self) -> tuple[tuple[str, str], ...]:
        return ((self.VALIDATION_PROVIDER_GODADDY, _("GoDaddy DNS")),)

    def _message_manual_dns_steps(self, request, config: SiteConfiguration, error_message: str) -> None:
        """Display operator instructions when DNS validation cannot continue automatically."""
        if "GoDaddy DNS" not in error_message and "godaddy" not in error_message.lower():
            return
        self.message_user(
            request,
            _(
                "%(config)s: Manual step required for GoDaddy DNS validation. "
                "Install certbot-dns-godaddy, verify DNS Credentials in Admin > DNS > DNS Credentials, "
                "and retry certificate provisioning."
            )
            % {"config": config},
            messages.WARNING,
        )

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

    @property
    def default_validation_provider(self) -> str:
        return self.VALIDATION_PROVIDER_GODADDY

    @admin.action(description=_("Generate certificates"))
    def generate_certificates(self, request, queryset):
        self._generate_certificates(request, queryset)
