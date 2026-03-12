"""Status reporting helpers for the HTTPS command."""

from __future__ import annotations

from apps.certs.models import CertbotCertificate
from apps.nginx.management.commands.https_parts.verification import _verify_certificate
from apps.nginx.models import SiteConfiguration


def _render_report(
    service,
    *,
    sudo: str,
    domain_filter: str | None = None,
    require_godaddy: bool = False,
    require_local: bool = False,
) -> None:
    """Render HTTPS status report with detailed certificate verification output."""

    configs = SiteConfiguration.objects.select_related(
        "certificate",
        "certificate__certbotcertificate",
        "certificate__selfsignedcertificate",
    ).order_by("pk")

    if domain_filter:
        configs = configs.filter(name=domain_filter)
    if require_godaddy:
        configs = configs.filter(
            certificate__certbotcertificate__challenge_type=CertbotCertificate.ChallengeType.GODADDY
        )
    elif require_local:
        configs = configs.filter(certificate__selfsignedcertificate__isnull=False)

    config_list = list(configs)
    if not config_list:
        if domain_filter:
            service.stdout.write(f"No site configurations found for {domain_filter}.")
        else:
            service.stdout.write("No site configurations found.")
        return

    service.stdout.write("HTTPS status report:")
    for config in config_list:
        cert = config.certificate
        cert_label = "none"
        if cert:
            cert_label = f"{cert.name} ({cert.__class__.__name__})"
        service.stdout.write(
            f"- {config.name}: protocol={config.protocol}, enabled={config.enabled}, certificate={cert_label}"
        )
        if cert:
            for line in _verify_certificate(cert, sudo=sudo):
                service.stdout.write(f"  - {line}")
