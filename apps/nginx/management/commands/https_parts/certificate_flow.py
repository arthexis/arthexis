"""Certificate provisioning helpers for the HTTPS command."""

from __future__ import annotations

from getpass import getpass
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
from apps.dns.models import DNSProviderCredential
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


def _validate_godaddy_setup(
    service,
    certificate,
    *,
    key: str | None = None,
) -> None:
    """Validate GoDaddy DNS challenge prerequisites before provisioning."""

    if not isinstance(certificate._specific_certificate, CertbotCertificate):
        return
    certbot = certificate._specific_certificate
    if certbot.challenge_type != CertbotCertificate.ChallengeType.GODADDY:
        return

    credential = certbot.dns_credential
    if key:
        credential = _resolve_godaddy_credential(key=key)
        if credential is None:
            raise CommandError(
                f"GoDaddy credential '{key}' was not found or is disabled. "
                "Configure it with './command.sh godaddy setup ...' and retry."
            )
    elif not (
        credential
        and credential.is_enabled
        and credential.provider == DNSProviderCredential.Provider.GODADDY
    ):
        credential = _resolve_godaddy_credential(key=None)
        if credential is None:
            credential = _prompt_for_godaddy_credential(service, certbot.domain)
        if credential is None:
            raise CommandError(
                "GoDaddy DNS validation requires credentials. Re-run with an interactive terminal or configure DNS > DNS Credentials in admin."
            )
    certbot.dns_credential = credential
    certbot.save(update_fields=["dns_credential", "updated_at"])
    service.stdout.write(
        "Using GoDaddy credential '%s'. Ensure certbot and Python requests are available to run DNS hooks."
        % credential
    )


def _resolve_godaddy_credential(*, key: str | None = None) -> DNSProviderCredential | None:
    """Resolve an enabled GoDaddy credential by selector or default ordering."""

    queryset = DNSProviderCredential.objects.filter(
        provider=DNSProviderCredential.Provider.GODADDY,
        is_enabled=True,
    ).order_by("pk")
    if not key:
        return queryset.first()

    if key.isdigit():
        credential = queryset.filter(pk=int(key)).first()
        if credential:
            return credential

    for credential in queryset:
        if (credential.resolve_sigils("api_key") or "").strip() == key:
            return credential
    return None


def _prompt_for_godaddy_credential(service, domain: str) -> DNSProviderCredential | None:
    """Prompt for GoDaddy credentials and persist them for DNS-01 validation."""

    if not sys.stdin.isatty():
        service.stdout.write("No enabled GoDaddy DNS credential was found.")
        service.stdout.write(
            "Create one in admin (DNS > DNS Credentials) or re-run this command in an interactive terminal to enter credentials now."
        )
        return None

    service.stdout.write("No enabled GoDaddy DNS credential was found.")
    service.stdout.write("Create API credentials in GoDaddy: Developer Portal -> API Keys -> Create New Key.")
    service.stdout.write("Docs: https://developer.godaddy.com/keys")
    should_continue = input("Enter credentials now and save to DNS Credentials? [y/N]: ").strip().lower()
    if should_continue not in {"y", "yes"}:
        return None

    api_key = getpass("GoDaddy API key: ").strip()
    api_secret = getpass("GoDaddy API secret: ").strip()
    customer_id = input("GoDaddy customer ID (optional): ").strip()
    use_sandbox = input("Use GoDaddy OTE sandbox environment? [y/N]: ").strip().lower()

    if not api_key or not api_secret:
        service.stdout.write("API key and secret are required to save credentials.")
        return None

    credential = DNSProviderCredential.objects.create(
        provider=DNSProviderCredential.Provider.GODADDY,
        api_key=api_key,
        api_secret=api_secret,
        customer_id=customer_id,
        default_domain=domain,
        use_sandbox=use_sandbox in {"y", "yes"},
        is_enabled=True,
    )
    service.stdout.write(
        service.style.SUCCESS("Saved GoDaddy DNS credential for automated DNS validation.")
    )
    return credential


def _provision_certificate(
    service,
    *,
    domain: str,
    config: SiteConfiguration,
    certificate,
    use_local: bool,
    use_godaddy: bool,
    sandbox_override: bool | None,
    sudo: str,
    reload: bool,
    force_renewal: bool,
    godaddy_credential_key: str | None,
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
            _validate_godaddy_setup(
                service,
                certificate,
                key=godaddy_credential_key,
            )
        certificate.provision(
            sudo=sudo,
            dns_use_sandbox=sandbox_override,
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
