from __future__ import annotations

import ipaddress
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.certs.models import CertificateBase, CertbotCertificate, SelfSignedCertificate
from apps.nginx.config_utils import slugify
from apps.nginx.models import SiteConfiguration
from apps.nginx.management.commands._config_selection import get_configurations


class Command(BaseCommand):
    help = "Create and provision local certificates for selected nginx configurations."  # noqa: A003
    CERTIFICATE_TYPE_SELF_SIGNED = "self-signed"
    CERTIFICATE_TYPE_CERTBOT = "certbot"

    def add_arguments(self, parser):
        parser.add_argument(
            "--ids",
            default="",
            help="Comma-separated SiteConfiguration ids to provision certificates for.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Provision certificates for all site configurations.",
        )
        parser.set_defaults(certificate_type=self.CERTIFICATE_TYPE_SELF_SIGNED)
        certificate_type_group = parser.add_mutually_exclusive_group()
        certificate_type_group.add_argument(
            "--type",
            "--certificate-type",
            dest="certificate_type",
            choices=(self.CERTIFICATE_TYPE_SELF_SIGNED, self.CERTIFICATE_TYPE_CERTBOT),
            help="Certificate type to create when a site configuration is missing one.",
        )
        certificate_type_group.add_argument(
            "--certbot",
            action="store_const",
            const=self.CERTIFICATE_TYPE_CERTBOT,
            dest="certificate_type",
            help="Create certbot certificates when a site configuration is missing one.",
        )
        certificate_type_group.add_argument(
            "--self-signed",
            action="store_const",
            const=self.CERTIFICATE_TYPE_SELF_SIGNED,
            dest="certificate_type",
            help="Create self-signed certificates when a site configuration is missing one.",
        )

    def handle(self, *args, **options):
        queryset = get_configurations(options["ids"], select_all=options["all"])
        configs = list(queryset)
        if not configs:
            self._render_available_sites()
            raise CommandError("No site configurations selected. Use --ids or --all.")

        certificate_type = options["certificate_type"]
        errors: list[str] = []
        for config in configs:
            if config.protocol != "https":
                self.stdout.write(f"{config}: HTTPS is not enabled; skipping certificate provisioning.")
                continue

            certificate: CertificateBase | None = config.certificate
            if certificate is None:
                certificate = self._create_certificate_for_config(
                    config, certificate_type=certificate_type
                )
                self.stdout.write(
                    f"{config}: Created a {self._certificate_type_label(certificate_type)} certificate for "
                    f"{certificate.domain}."
                )

            try:
                message = certificate.provision()
            except Exception as exc:  # pragma: no cover - provisioning errors
                error_message = f"{config}: {exc}"
                self.stderr.write(self.style.ERROR(error_message))
                errors.append(error_message)
            else:
                self.stdout.write(self.style.SUCCESS(f"{config}: {message}"))

        if errors:
            raise CommandError("One or more certificates failed to provision. Review the output above.")

    def _create_certificate_for_config(
        self, config, *, certificate_type: str
    ) -> CertificateBase:
        if certificate_type == self.CERTIFICATE_TYPE_CERTBOT:
            return self._create_certbot_certificate_for_config(config)
        return self._create_self_signed_certificate_for_config(config)

    def _create_self_signed_certificate_for_config(self, config) -> CertificateBase:
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

    def _create_certbot_certificate_for_config(self, config) -> CertificateBase:
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

    def _certificate_type_label(self, certificate_type: str) -> str:
        if certificate_type == self.CERTIFICATE_TYPE_CERTBOT:
            return "certbot"
        return "self-signed"

    def _render_available_sites(self) -> None:
        available = list(SiteConfiguration.objects.all().order_by("pk"))
        if not available:
            self.stdout.write("No site configurations are available.")
            return
        self.stdout.write("Available site configurations:")
        for config in available:
            name = config.name or "unnamed"
            self.stdout.write(f"  [{config.pk}] {name}")
