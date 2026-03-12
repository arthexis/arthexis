"""Renewal workflow helpers for tracked certificates."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shlex

from django.core.management.base import CommandError
from django.utils import timezone

from apps.certs.models import CertificateBase, CertbotCertificate
from apps.nginx.management.commands.https_parts.config_apply import _apply_config
from apps.nginx.models import SiteConfiguration


def _format_expiration(value: datetime | None) -> str:
    """Format certificate expiration timestamps for operator-facing output."""

    if value is None:
        return "unknown"
    return value.isoformat(timespec="seconds")


def _certificate_source_label(certificate: CertificateBase) -> str:
    """Return a concise source label for a certificate record."""

    certbot_record = getattr(certificate, "certbotcertificate", None)
    if certbot_record is not None:
        if certbot_record.challenge_type == CertbotCertificate.ChallengeType.GODADDY:
            return "certbot (godaddy dns-01)"
        return "certbot (http-01)"
    if getattr(certificate, "selfsignedcertificate", None) is not None:
        return "self-signed"
    return "certificate"


def _renew_due_certificates(
    service,
    *,
    sudo: str,
    reload: bool,
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

    renewed_certificates: list[CertificateBase] = []
    errors: list[str] = []

    for certificate in due_certificates:
        previous_expiration = certificate.expiration_date
        try:
            certificate.renew(sudo=sudo)
        except RuntimeError as exc:
            errors.append(f"{certificate}: {exc}")
            continue

        certificate.refresh_from_db(fields=["expiration_date", "updated_at"])
        renewed_certificates.append(certificate)

        service.stdout.write(
            service.style.SUCCESS(
                "Renewed certificate: "
                f"domain={certificate.domain}; "
                f"source={_certificate_source_label(certificate)}; "
                f"expiration={_format_expiration(previous_expiration)}"
                f" -> {_format_expiration(certificate.expiration_date)}; "
                f"cert={certificate.certificate_path}; "
                f"key={certificate.certificate_key_path}"
            )
        )

    renewed = len(renewed_certificates)
    if renewed:
        https_configs = list(
            SiteConfiguration.objects.filter(
                certificate_id__in=[certificate.pk for certificate in renewed_certificates],
                enabled=True,
                protocol="https",
            ).order_by("name")
        )
        for index, config in enumerate(https_configs):
            _apply_config(
                service,
                config,
                reload=reload and index == len(https_configs) - 1,
            )
        if https_configs:
            config_names = ", ".join(config.name for config in https_configs)
            action_label = "Reloaded" if reload else "Applied without reload"
            service.stdout.write(
                f"{action_label} HTTPS site configuration(s): {config_names}."
            )
        service.stdout.write(service.style.SUCCESS(f"Renewed {renewed} certificate(s)."))

    if errors:
        raise CommandError("Certificate renewal failed:\n" + "\n".join(errors))
    if not renewed:
        service.stdout.write("No certificates were due for renewal.")
