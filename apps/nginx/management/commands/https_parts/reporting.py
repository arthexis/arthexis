"""Status report rendering for the https command."""

from __future__ import annotations

from apps.nginx.models import SiteConfiguration


def _render_report(service, *, sudo: str) -> None:
    """Render a report of configured nginx site HTTPS state."""

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
            cert_summary = service.verify_certificate(cert, sudo=sudo)
        service.stdout.write(
            f"- {config.name}: protocol={config.protocol}, enabled={config.enabled}, "
            f"certificate={cert_label}"
        )
        if cert_summary:
            service.stdout.write(f"  - {cert_summary}")
