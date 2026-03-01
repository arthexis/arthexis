"""Certificate verification and expiry warning helpers."""

from __future__ import annotations

from datetime import timedelta
import shlex

from django.utils import timezone

from apps.certs.models import CertbotCertificate
from apps.certs.services import CertificateVerificationResult


def _verify_certificate(cert, *, sudo: str) -> str:
    """Verify a certificate and return formatted status output."""

    result = cert.verify(sudo=sudo)
    return _format_verification_result(result)


def _format_verification_result(result: CertificateVerificationResult) -> str:
    """Render verification result with stable command output wording."""

    status = "valid" if result.ok else "invalid"
    summary = result.summary
    return f"Certificate status: {status}. {summary}"


def _warn_if_certificate_expiring_soon(service, certificate, *, warn_days: int) -> None:
    """Emit actionable guidance when certificate expiration is near or in the past."""

    expiration = getattr(certificate, "expiration_date", None)
    if expiration is None:
        return

    now = timezone.now()
    threshold = now + timedelta(days=warn_days)
    if expiration <= threshold:
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
        else:
            status = "expires soon"
            if is_certbot:
                remediation = (
                    "Run './command.sh https --enable --force-renewal "
                    f"--certbot {quoted_domain}' (or '--godaddy {quoted_domain}') to reissue immediately."
                )
            else:
                remediation = "Run './command.sh https --enable --local' to reissue immediately."

        service.stdout.write(
            service.style.WARNING(
                f"Certificate for {certificate.domain} {status} at {expiration.isoformat()}. {remediation}"
            )
        )
