"""Certificate inventory helpers for the HTTPS command.

Public ACME/Certbot lifecycle belongs to gway-web. Arthexis keeps certificate
inventory/path binding plus application-owned self-signed certificate support.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError

from apps.certs.models import CertificateBase, SelfSignedCertificate
from apps.nginx.config_utils import slugify
from apps.nginx.models import SiteConfiguration


def _get_or_create_certificate(
    domain: str,
    config: SiteConfiguration,
    *,
    use_local: bool,
):
    """Create or update the certificate inventory record for an HTTPS site."""

    slug = slugify(domain)
    if use_local:
        base_path = (
            Path(settings.BASE_DIR) / "scripts" / "generated" / "certificates" / slug
        )
        defaults = {
            "domain": domain,
            "certificate_path": str(base_path / "fullchain.pem"),
            "certificate_key_path": str(base_path / "privkey.pem"),
        }
        certificate, _ = SelfSignedCertificate.objects.update_or_create(
            name="local-https-localhost",
            defaults=defaults,
        )
        return certificate

    defaults = {
        "domain": domain,
        "certificate_path": f"/etc/letsencrypt/live/{domain}/fullchain.pem",
        "certificate_key_path": f"/etc/letsencrypt/live/{domain}/privkey.pem",
    }
    certificate, created = CertificateBase.objects.get_or_create(
        name=f"{config.name or 'nginx-site'}-{slug}-public",
        defaults=defaults,
    )
    if not created:
        updated_fields: list[str] = []
        for field, value in defaults.items():
            if getattr(certificate, field) != value:
                setattr(certificate, field, value)
                updated_fields.append(field)
        if updated_fields:
            certificate.save(update_fields=[*updated_fields, "updated_at"])
    return certificate


def _provision_certificate(
    service,
    *,
    domain: str,
    config: SiteConfiguration,
    certificate,
    use_local: bool,
    sudo: str,
    reload: bool,
    force_renewal: bool,
) -> None:
    """Generate local certificates; public issuance is delegated to gway-web."""

    del service, domain, config, reload
    if use_local:
        certificate.generate(
            sudo=sudo,
            subject_alt_names=["localhost", "127.0.0.1", "::1"],
        )
        return

    if force_renewal:
        raise CommandError(
            "Public certificate renewal is managed by gway-web; run the web cert/certbot flow there."
        )
