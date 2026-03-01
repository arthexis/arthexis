"""Certificate provisioning flow steps for the https command."""

from __future__ import annotations

import sys

from django.core.management.base import CommandError
from django.contrib.sites.models import Site

from apps.certs.models import CertbotCertificate
from apps.certs.services import CertbotChallengeError
from apps.nginx.models import SiteConfiguration
from apps.sites.site_config import update_local_nginx_scripts

from .config_apply import _apply_config, _get_or_create_config
from .constants import CERTBOT_HTTP01_BOOTSTRAP_MESSAGE, NGINX_CONFIGURE_REMEDIATION_TEMPLATE


def _prepare_http01_challenge_site(service, domain: str, *, reload: bool) -> None:
    """Ensure nginx can answer HTTP-01 challenges before certbot provisioning."""

    service.ensure_managed_site(domain, require_https=False)
    bootstrap_config = _get_or_create_config(domain, protocol="http")
    _apply_config(service, bootstrap_config, reload=reload)


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


def enable_https(
    service,
    domain: str,
    *,
    use_local: bool,
    use_godaddy: bool,
    sandbox_override: bool | None,
    sudo: str,
    reload: bool,
    force_renewal: bool,
    warn_days: int,
):
    """Provision certificate material and apply HTTPS site configuration."""

    config = _get_or_create_config(domain, protocol="https")
    certificate = service.get_or_create_certificate(
        domain,
        config,
        use_local=use_local,
        use_godaddy=use_godaddy,
    )

    if config.certificate_id != certificate.id:
        config.certificate = certificate
        config.save(update_fields=["certificate"])

    if use_local:
        certificate.generate(sudo=sudo, subject_alt_names=["localhost", "127.0.0.1", "::1"])
    else:
        http01_bootstrapped = False
        previous_certificate_path = ""
        previous_certificate_key_path = ""
        if force_renewal:
            previous_certificate_path = certificate.certificate_path
            previous_certificate_key_path = certificate.certificate_key_path

        try:
            if not use_godaddy:
                _prepare_http01_challenge_site(service, domain, reload=reload)
                http01_bootstrapped = True
            if use_godaddy:
                service.validate_godaddy_setup(certificate)
            certificate.provision(
                sudo=sudo,
                dns_use_sandbox=sandbox_override,
                force_renewal=force_renewal,
            )
        except CertbotChallengeError as exc:
            if http01_bootstrapped:
                _restore_https_config_after_http01_bootstrap(service, config, reload=reload)
                service.ensure_managed_site(domain, require_https=True)
            raise CommandError(
                _build_certbot_challenge_command_error(
                    domain=domain,
                    challenge_type=certificate.challenge_type,
                    reason=str(exc),
                )
            ) from exc

        if force_renewal:
            service.warn_if_certificate_paths_changed(
                certificate,
                previous_certificate_path=previous_certificate_path,
                previous_certificate_key_path=previous_certificate_key_path,
            )
            service.validate_force_renewal_result(certificate)

    service.warn_if_certificate_expiring_soon(certificate, warn_days=warn_days)
    SiteConfiguration.objects.filter(pk=config.pk).update(protocol="https", enabled=True)
    config.refresh_from_db(fields=["protocol", "enabled"])
    service.ensure_managed_site(domain, require_https=True)
    _apply_config(service, config, reload=reload)
    return certificate


def disable_https(service, domain: str, *, reload: bool) -> None:
    """Disable HTTPS by applying HTTP protocol for an existing site config."""

    config = service.get_existing_config(domain)
    if config is None:
        raise CommandError(f"No site configuration found for {domain}.")

    if config.protocol != "http":
        config.protocol = "http"
        config.save(update_fields=["protocol"])

    _apply_config(service, config, reload=reload)
    service.ensure_managed_site(domain, require_https=False)


def _restore_https_config_after_http01_bootstrap(
    service,
    config: SiteConfiguration,
    *,
    reload: bool,
) -> None:
    """Restore persisted/runtime protocol to HTTPS after HTTP-01 bootstrap."""

    SiteConfiguration.objects.filter(pk=config.pk).update(protocol="https", enabled=True)
    config.refresh_from_db(fields=["protocol", "enabled"])
    _apply_config(service, config, reload=reload)


def ensure_managed_site(domain: str, *, require_https: bool) -> None:
    """Persist the target domain as a managed Site and refresh staged nginx hosts."""

    if domain == "localhost":
        return
    site, created = Site.objects.get_or_create(domain=domain, defaults={"name": domain})
    updated_fields: list[str] = []

    if hasattr(site, "managed") and not getattr(site, "managed"):
        setattr(site, "managed", True)
        updated_fields.append("managed")
    if hasattr(site, "require_https") and getattr(site, "require_https") != require_https:
        setattr(site, "require_https", require_https)
        updated_fields.append("require_https")
    if created:
        site.save()
    elif updated_fields:
        site.save(update_fields=updated_fields)

    update_local_nginx_scripts()
