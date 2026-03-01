"""Renewal workflow helpers for tracked certificates."""

from __future__ import annotations

from pathlib import Path
import shlex

from django.core.management.base import CommandError
from django.utils import timezone

from apps.certs.models import CertificateBase, CertbotCertificate


def _renew_due_certificates(
    service,
    *,
    sudo: str,
    domain_filter: str | None = None,
    require_godaddy: bool = False,
    require_local: bool = False,
) -> None:
    """Renew certificates due for rotation after refreshing on-disk expiration metadata."""

    now = timezone.now()
    candidate_certificates = CertificateBase.objects.all().select_related(
        "certbotcertificate", "selfsignedcertificate"
    )

    if domain_filter:
        candidate_certificates = candidate_certificates.filter(domain=domain_filter)

    if require_godaddy:
        candidate_certificates = candidate_certificates.filter(
            certbotcertificate__challenge_type=CertbotCertificate.ChallengeType.GODADDY
        )
    elif require_local:
        candidate_certificates = candidate_certificates.filter(selfsignedcertificate__isnull=False)

    candidate_list = list(candidate_certificates)

    due_certificates: list[CertificateBase] = []

    for certificate in candidate_list:
        stored_expiration = certificate.expiration_date
        try:
            certificate.update_expiration_date(sudo=sudo)
        except RuntimeError as exc:
            service.stdout.write(
                service.style.WARNING(
                    f"Could not refresh expiration for {certificate.domain}: {exc}"
                )
            )
        refreshed_expiration = certificate.expiration_date

        if refreshed_expiration and refreshed_expiration <= now:
            due_certificates.append(certificate)
            continue

        certificate_file_missing = bool(certificate.certificate_path) and not Path(
            certificate.certificate_path
        ).exists()
        if refreshed_expiration is None and (
            certificate_file_missing
            or (stored_expiration is not None and stored_expiration <= now)
        ):
            due_certificates.append(certificate)

    if not due_certificates:
        if candidate_list:
            if domain_filter:
                service.stdout.write(f"No certificates were due for renewal for {domain_filter}.")
                quoted_domain = shlex.quote(domain_filter)
                service.stdout.write(
                    "To force immediate certbot reissuance, run: "
                    f"./command.sh https --enable --force-renewal --certbot {quoted_domain} "
                    f"(or --godaddy {quoted_domain})."
                )
            else:
                service.stdout.write("No certificates were due for renewal.")
        else:
            service.stdout.write("No certificates are tracked for renewal.")
        return

    renewed = 0
    errors: list[str] = []
    for certificate in due_certificates:
        try:
            certificate.renew(sudo=sudo)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{certificate}: {exc}")
            continue
        renewed += 1

    if renewed:
        service.stdout.write(service.style.SUCCESS(f"Renewed {renewed} certificate(s)."))

    if errors:
        raise CommandError("Certificate renewal failed:\n" + "\n".join(errors))
    if not renewed:
        service.stdout.write("No certificates were due for renewal.")
