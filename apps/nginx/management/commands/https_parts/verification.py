"""Certificate verification and expiry warning helpers."""

from __future__ import annotations

from datetime import timedelta
import shlex

from django.utils import timezone

from apps.certs.models import CertbotCertificate
from apps.certs.services import CertificateVerificationResult


def _verify_certificate(cert, *, sudo: str) -> list[str]:
    """Verify a certificate and return detailed formatted status output lines."""

    target_cert = getattr(cert, "_specific_certificate", cert)
    verify_paths = getattr(target_cert, "verify_paths", None)
    if callable(verify_paths):
        result = verify_paths(sudo=sudo)
    else:
        result = target_cert.verify(sudo=sudo)
    expiration = getattr(cert, "expiration_date", None)
    lines = _format_verification_result(result)

    if expiration is None:
        lines.append("Expiration: unknown (certificate metadata unavailable).")
    else:
        now = timezone.now()
        remaining = expiration - now
        days_remaining = remaining.days
        state = "expired" if remaining.total_seconds() <= 0 else "active"
        day_label = "day" if days_remaining == 1 else "days"
        lines.append(
            f"Expiration: {expiration.isoformat()} ({days_remaining} {day_label} remaining; {state})."
        )

    cert_path = getattr(cert, "certificate_path", "") or "unknown"
    key_path = getattr(cert, "certificate_key_path", "") or "unknown"
    lines.append(f"Paths: cert={cert_path}; key={key_path}.")
    return lines


def _format_verification_result(result: CertificateVerificationResult) -> list[str]:
    """Render verification result with stable command output wording."""

    status = "valid" if result.ok else "invalid"
    if not result.messages:
        return [f"Certificate status: {status}. Certificate verified."]
    return [f"Certificate status: {status}.", *result.messages]


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
            remediation = (
                "Run './command.sh https --renew' to reissue due certificates."
            )

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
                remediation = (
                    "Run './command.sh https --enable --local' to reissue immediately."
                )

        service.stdout.write(
            service.style.WARNING(
                f"Certificate for {certificate.domain} {status} at {expiration.isoformat()}. {remediation}"
            )
        )
