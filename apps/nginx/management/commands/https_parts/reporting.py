"""Status reporting helpers for the HTTPS command."""

from __future__ import annotations

from apps.nginx.management.commands.https_parts.verification import _verify_certificate
from apps.nginx.models import SiteConfiguration


def _render_report(service, *, sudo: str) -> None:
    """Render HTTPS status report with optional certificate verification details."""

    configs = list(SiteConfiguration.objects.order_by("pk"))
    if not configs:
        service.stdout.write("No site configurations found.")
        return

    service.stdout.write("HTTPS status report:")
    for config in configs:
        cert = config.certificate
        cert_label = "none"
        cert_summary = ""
        if cert:
            cert_label = f"{cert.name} ({cert.__class__.__name__})"
            cert_summary = _verify_certificate(cert, sudo=sudo)
        service.stdout.write(
            f"- {config.name}: protocol={config.protocol}, enabled={config.enabled}, certificate={cert_label}"
        )
        if cert_summary:
            service.stdout.write(f"  - {cert_summary}")
