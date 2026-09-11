"""Certificate provisioning helpers for the HTTPS command.

Public-web ACME/Certbot lifecycle is owned by gway-web. Arthexis keeps this
module only for application-owned self-signed certificate provisioning used by
legacy/local HTTPS flows.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError

from apps.certs.models import SelfSignedCertificate
from apps.nginx.config_utils import slugify
from apps.nginx.models import SiteConfiguration

_PUBLIC_TLS_MOVED_MESSAGE = (
    "Public-web certificate provisioning moved to gway-web. "
    "Configure the site with 'gway web site' and use its certificate provider "
    "or --certbot support instead."
)


def _get_or_create_certificate(
    domain: str,
    config: SiteConfiguration,
    *,
    use_local: bool,
):
    """Create or update the self-signed certificate used by local HTTPS."""

    if not use_local:
        raise CommandError(_PUBLIC_TLS_MOVED_MESSAGE)

    slug = slugify(domain)
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
    """Provision application-owned self-signed certificate material."""

    if not use_local:
        raise CommandError(_PUBLIC_TLS_MOVED_MESSAGE)

    certificate.generate(
        sudo=sudo,
        subject_alt_names=["localhost", "127.0.0.1", "::1"],
    )
