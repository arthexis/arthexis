"""Certificate provisioning helpers for the HTTPS command."""

from __future__ import annotations

from pathlib import Path
import sys

from django.conf import settings
from django.core.management.base import CommandError
from django.utils import timezone

from apps.certs.models import CertbotCertificate, SelfSignedCertificate
from apps.certs.services import (
    CertbotChallengeError,
    CertbotError,
    ensure_certbot_available,
)
from apps.nginx.config_utils import slugify
from apps.nginx.management.commands.https_parts.config_apply import _apply_config, _get_or_create_config
from apps.nginx.management.commands.https_parts.constants import (
    CERTBOT_HTTP01_BOOTSTRAP_MESSAGE,
    FORCE_RENEWAL_EXPIRATION_UNAVAILABLE_WARNING,
    FORCE_RENEWAL_STILL_EXPIRED_ERROR,
    NGINX_CONFIGURE_REMEDIATION_TEMPLATE,
)
from apps.nginx.models import SiteConfiguration


def _prepare_http01_challenge_site(service, domain: str, *, reload: bool) -> None:
    """Ensure nginx can answer HTTP-01 challenges before certbot provisioning."""

    service._ensure_managed_site(domain, require_https=False)
    bootstrap_config = _get_or_create_config(domain, protocol="http")
    _apply_config(service, bootstrap_config, reload=reload)


def _restore_https_config_after_http01_bootstrap(
    service,
    config: SiteConfiguration,
    *,
    reload: bool,
) -> None:
    """Restore persisted/runtime site protocol to HTTPS after HTTP-01 bootstrap."""

    SiteConfiguration.objects.filter(pk=config.pk).update(protocol="https", enabled=True)
    config.refresh_from_db(fields=["protocol", "enabled"])
    _apply_config(service, config, reload=reload)


def _build_certbot_challenge_command_error(
    *,
    domain: str,
    challenge_type: str,
    reason: str,
) -> str:
    """Return actionable command output for certbot ACME challenge failures."""

    hints = [
        f"HTTPS enable did not complete for {domain}.",
        reason,
    ]
    if challenge_type == CertbotCertificate.ChallengeType.NGINX:
        hints.extend(
            [
                CERTBOT_HTTP01_BOOTSTRAP_MESSAGE,
                NGINX_CONFIGURE_REMEDIATION_TEMPLATE.format(command=sys.argv[0]),
            ]
        )
    return "\n".join(hints)


def _validate_force_renewal_result(service, certificate) -> None:
    """Raise when force-renewal returns but the certificate is still expired."""

    expiration = getattr(certificate, "expiration_date", None)
    if expiration is None:
        domain = getattr(certificate, "domain", "the requested domain")
        service.stdout.write(
            service.style.WARNING(
                FORCE_RENEWAL_EXPIRATION_UNAVAILABLE_WARNING.format(domain=domain)
            )
        )
        return

    if expiration <= timezone.now():
        raise CommandError(FORCE_RENEWAL_STILL_EXPIRED_ERROR)


def _warn_if_certificate_paths_changed(
    service,
    certificate,
    *,
    previous_certificate_path: str,
    previous_certificate_key_path: str,
) -> None:
    """Surface certbot lineage path changes so operators can verify consumers."""

    if (
        certificate.certificate_path == previous_certificate_path
        and certificate.certificate_key_path == previous_certificate_key_path
    ):
        return

    service.stdout.write(
        service.style.WARNING(
            "Certificate storage paths changed after certbot issuance: "
            f"cert old={previous_certificate_path} new={certificate.certificate_path}; "
            f"key old={previous_certificate_key_path} new={certificate.certificate_key_path}. "
            "If external services reference old paths, reload or update them accordingly."
        )
    )


def _get_or_create_certificate(domain: str, config: SiteConfiguration, *, use_local: bool, use_godaddy: bool):
    """Create or update certificate records used by HTTPS provisioning."""

    slug = slugify(domain)
    if use_local:
        base_path = Path(settings.BASE_DIR) / "scripts" / "generated" / "certificates" / slug
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

    challenge_type = (
        CertbotCertificate.ChallengeType.GODADDY
        if use_godaddy
        else CertbotCertificate.ChallengeType.NGINX
    )
    certificate, created = CertbotCertificate.objects.get_or_create(
        name=f"{config.name or 'nginx-site'}-{slug}-certbot",
        defaults={
            "domain": domain,
            "certificate_path": f"/etc/letsencrypt/live/{domain}/fullchain.pem",
            "certificate_key_path": f"/etc/letsencrypt/live/{domain}/privkey.pem",
            "challenge_type": challenge_type,
        },
    )

    if not created:
        updated_fields: list[str] = []
        if certificate.domain != domain:
            certificate.domain = domain
            updated_fields.append("domain")
        if certificate.challenge_type != challenge_type:
            certificate.challenge_type = challenge_type
            updated_fields.append("challenge_type")
        if not certificate.certificate_path:
            certificate.certificate_path = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
            updated_fields.append("certificate_path")
        if not certificate.certificate_key_path:
            certificate.certificate_key_path = f"/etc/letsencrypt/live/{domain}/privkey.pem"
            updated_fields.append("certificate_key_path")
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
    use_godaddy: bool,
    sudo: str,
    reload: bool,
    force_renewal: bool,
) -> None:
    """Provision local or certbot certificates and handle recovery/warnings."""

    if use_local:
        certificate.generate(
            sudo=sudo,
            subject_alt_names=["localhost", "127.0.0.1", "::1"],
        )
        return

    try:
        ensure_certbot_available(sudo=sudo)
    except CertbotError as exc:
        raise CommandError(str(exc)) from exc

    http01_bootstrapped = False
    if force_renewal:
        previous_certificate_path = certificate.certificate_path
        previous_certificate_key_path = certificate.certificate_key_path

    try:
        if not use_godaddy:
            http01_bootstrapped = True
            _prepare_http01_challenge_site(service, domain, reload=reload)
        if use_godaddy:
            raise CommandError(
                "GoDaddy DNS automation is no longer supported. Configure DNS manually and use --certbot."
            )
        certificate.provision(
            sudo=sudo,
            dns_use_sandbox=None,
            force_renewal=force_renewal,
        )
    except Exception as exc:  # noqa: BLE001
        if http01_bootstrapped:
            _restore_https_config_after_http01_bootstrap(service, config, reload=reload)
        if isinstance(exc, CertbotChallengeError):
            raise CommandError(
                _build_certbot_challenge_command_error(
                    domain=domain,
                    challenge_type=certificate.challenge_type,
                    reason=str(exc),
                )
            ) from exc
        raise

    if force_renewal:
        _warn_if_certificate_paths_changed(
            service,
            certificate,
            previous_certificate_path=previous_certificate_path,
            previous_certificate_key_path=previous_certificate_key_path,
        )
        _validate_force_renewal_result(service, certificate)
