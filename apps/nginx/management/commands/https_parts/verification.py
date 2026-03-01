"""Certificate verification and warning helpers for the https command."""

from __future__ import annotations

from datetime import timedelta
import shlex

from django.core.management.base import CommandError
from django.utils import timezone

from apps.certs.models import CertbotCertificate
from apps.certs.services import CertificateVerificationResult

from .constants import (
    FORCE_RENEWAL_EXPIRATION_UNAVAILABLE_WARNING,
    FORCE_RENEWAL_STILL_EXPIRED_ERROR,
)


def _verify_certificate(cert, *, sudo: str) -> str:
    """Run certificate verification and return a rendered summary."""

    result = cert.verify(sudo=sudo)
    return _format_verification_result(result)


def _format_verification_result(result: CertificateVerificationResult) -> str:
    """Format certificate verification state into a stable summary string."""

    status = "valid" if result.ok else "invalid"
    return f"Certificate status: {status}. {result.summary}"


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


def _warn_if_certificate_expiring_soon(service, certificate, *, warn_days: int) -> None:
    """Emit actionable guidance when certificate expiration is near or in the past."""

    expiration = getattr(certificate, "expiration_date", None)
    if expiration is None:
        return

    now = timezone.now()
    threshold = now + timedelta(days=warn_days)
    if expiration > threshold:
        return

    is_certbot = isinstance(certificate, CertbotCertificate)
    quoted_domain = shlex.quote(certificate.domain)

    if expiration <= now:
        status = "has expired"
        remediation = "Run './command.sh https --renew' to reissue due certificates."
        if is_certbot:
            remediation += (
                " Use './command.sh https --enable --force-renewal "
                f"--certbot {quoted_domain}' (or '--godaddy {quoted_domain}') "
                "only when you need to force immediate reissuance."
            )
    elif is_certbot:
        status = "expires soon"
        remediation = (
            "Run './command.sh https --enable --force-renewal "
            f"--certbot {quoted_domain}' (or '--godaddy {quoted_domain}') to reissue immediately."
        )
    else:
        status = "expires soon"
        remediation = "Run './command.sh https --enable --local' to reissue immediately."

    service.stdout.write(
        service.style.WARNING(
            f"Certificate for {certificate.domain} {status} at {expiration.isoformat()}. {remediation}"
        )
    )
