"""Service object orchestrating the nginx https command behavior."""

from __future__ import annotations

from dataclasses import dataclass
from getpass import getpass
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError

from apps.certs.models import CertbotCertificate, SelfSignedCertificate
from apps.dns.models import DNSProviderCredential
from apps.nginx.config_utils import slugify
from apps.nginx.models import SiteConfiguration

from . import certificate_flow, config_apply, parsing, renewal, reporting, verification


@dataclass(slots=True)
class HttpsProvisioningService:
    """Shared command state and orchestration for HTTPS command actions."""

    command: object

    @property
    def stdout(self):
        """Expose command stdout writer."""

        return self.command.stdout

    @property
    def style(self):
        """Expose command style formatter."""

        return self.command.style

    def run(self, options: dict[str, object]) -> None:
        """Dispatch https command actions and normalize implicit enable behavior."""

        enable = options["enable"]
        disable = options["disable"]
        renew_action = options["renew"]
        certbot_domain = options["certbot"]
        godaddy_domain = options["godaddy"]
        explicit_site = options["site"]
        parsed_site = parsing._parse_site_domain(explicit_site) if explicit_site else None

        if parsed_site and options["local"]:
            raise CommandError("--local cannot be combined with --site. Use --certbot/--godaddy or omit --local.")

        certbot_domain = certbot_domain or (parsed_site if parsed_site and not godaddy_domain else None)
        certbot_domain = parsing._parse_site_domain(certbot_domain) if certbot_domain else None
        godaddy_domain = parsing._parse_site_domain(godaddy_domain) if godaddy_domain else None
        use_local = options["local"] or not (certbot_domain or godaddy_domain)
        use_godaddy = bool(godaddy_domain)
        sandbox_override = parsing._parse_sandbox_override(options)
        reload = not options["no_reload"]
        sudo = "" if options["no_sudo"] else "sudo"
        force_renewal = options["force_renewal"]
        warn_days = options["warn_days"]

        if warn_days < 0:
            raise CommandError("--warn-days must be zero or a positive integer.")

        if use_local and force_renewal:
            raise CommandError("--force-renewal is only supported for certbot/godaddy certificates.")

        if not enable and not disable and not renew_action and (certbot_domain or godaddy_domain or parsed_site):
            enable = True

        if not enable and not disable and not renew_action:
            if options["local"]:
                raise CommandError("Use --enable or --disable with certificate options.")
            reporting._render_report(self, sudo=sudo)
            return

        domain = "localhost" if use_local else (godaddy_domain or certbot_domain)
        if not domain:
            raise CommandError("No target domain was provided. Use --site, --certbot, --godaddy, or --local.")

        if disable:
            certificate_flow.disable_https(self, domain, reload=reload)
            return

        if renew_action:
            renewal._renew_due_certificates(
                self,
                sudo=sudo,
                domain_filter=godaddy_domain or certbot_domain,
                require_godaddy=bool(godaddy_domain),
                require_local=bool(options["local"]),
            )
            return

        certificate = certificate_flow.enable_https(
            self,
            domain,
            use_local=use_local,
            use_godaddy=use_godaddy,
            sandbox_override=sandbox_override,
            sudo=sudo,
            reload=reload,
            force_renewal=force_renewal,
            warn_days=warn_days,
        )
        self.stdout.write(self.style.SUCCESS(f"HTTPS enabled for {domain} using {certificate.__class__.__name__}."))

    def get_existing_config(self, domain: str) -> SiteConfiguration | None:
        """Delegate config lookup."""

        return config_apply._get_existing_config(domain)

    def ensure_managed_site(self, domain: str, *, require_https: bool) -> None:
        """Delegate managed-site persistence."""

        certificate_flow.ensure_managed_site(domain, require_https=require_https)

    def verify_certificate(self, cert, *, sudo: str) -> str:
        """Delegate certificate verification for report rendering."""

        return verification._verify_certificate(cert, sudo=sudo)

    def warn_if_certificate_paths_changed(
        self,
        certificate,
        *,
        previous_certificate_path: str,
        previous_certificate_key_path: str,
    ) -> None:
        """Delegate warning for certbot lineage path changes."""

        verification._warn_if_certificate_paths_changed(
            self,
            certificate,
            previous_certificate_path=previous_certificate_path,
            previous_certificate_key_path=previous_certificate_key_path,
        )

    def validate_force_renewal_result(self, certificate) -> None:
        """Delegate post force-renewal expiration validation."""

        verification._validate_force_renewal_result(self, certificate)

    def warn_if_certificate_expiring_soon(self, certificate, *, warn_days: int) -> None:
        """Delegate expiry warning rendering."""

        verification._warn_if_certificate_expiring_soon(self, certificate, warn_days=warn_days)

    def get_or_create_certificate(
        self,
        domain: str,
        config: SiteConfiguration,
        *,
        use_local: bool,
        use_godaddy: bool,
    ):
        """Fetch or create a certificate model matching CLI intent."""

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

        if created:
            return certificate

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

    def validate_godaddy_setup(self, certificate) -> None:
        """Validate GoDaddy DNS challenge prerequisites before provisioning."""

        if not isinstance(certificate._specific_certificate, CertbotCertificate):
            return
        certbot = certificate._specific_certificate
        if certbot.challenge_type != CertbotCertificate.ChallengeType.GODADDY:
            return

        credential = certbot.dns_credential
        if not (
            credential
            and credential.is_enabled
            and credential.provider == DNSProviderCredential.Provider.GODADDY
        ):
            credential = (
                DNSProviderCredential.objects.filter(
                    provider=DNSProviderCredential.Provider.GODADDY,
                    is_enabled=True,
                )
                .order_by("pk")
                .first()
            )
        if credential is None:
            credential = self.prompt_for_godaddy_credential(certbot.domain)
            if credential is None:
                raise CommandError(
                    "GoDaddy DNS validation requires credentials. Re-run with an interactive terminal or configure DNS > DNS Credentials in admin."
                )
        certbot.dns_credential = credential
        certbot.save(update_fields=["dns_credential", "updated_at"])
        self.stdout.write(
            "Using GoDaddy credential '%s'. Ensure certbot and Python requests are available to run DNS hooks."
            % credential
        )

    def prompt_for_godaddy_credential(self, domain: str) -> DNSProviderCredential | None:
        """Prompt for GoDaddy credentials and persist them for DNS-01 validation."""

        import sys

        if not sys.stdin.isatty():
            self.stdout.write("No enabled GoDaddy DNS credential was found.")
            self.stdout.write(
                "Create one in admin (DNS > DNS Credentials) or re-run this command in an interactive terminal to enter credentials now."
            )
            return None

        self.stdout.write("No enabled GoDaddy DNS credential was found.")
        self.stdout.write("Create API credentials in GoDaddy: Developer Portal -> API Keys -> Create New Key.")
        self.stdout.write("Docs: https://developer.godaddy.com/keys")
        should_continue = input("Enter credentials now and save to DNS Credentials? [y/N]: ").strip().lower()
        if should_continue not in {"y", "yes"}:
            return None

        api_key = input("GoDaddy API key: ").strip()
        api_secret = getpass("GoDaddy API secret: ").strip()
        customer_id = input("GoDaddy customer ID (optional): ").strip()
        use_sandbox = input("Use GoDaddy OTE sandbox environment? [y/N]: ").strip().lower()

        if not api_key or not api_secret:
            self.stdout.write("API key and secret are required to save credentials.")
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
        self.stdout.write(self.style.SUCCESS("Saved GoDaddy DNS credential for automated DNS validation."))
        return credential
