from __future__ import annotations

from getpass import getpass
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from django.utils import timezone

from apps.certs.models import CertificateBase, CertbotCertificate, SelfSignedCertificate
from apps.dns.models import DNSProviderCredential
from apps.certs.services import CertificateVerificationResult
from apps.nginx.config_utils import slugify
from apps.nginx.models import SiteConfiguration
from apps.nginx.services import NginxUnavailableError, ValidationError


class Command(BaseCommand):
    help = "Manage HTTPS certificates and nginx configuration."

    def add_arguments(self, parser):
        action_group = parser.add_mutually_exclusive_group()
        action_group.add_argument(
            "--enable",
            action="store_true",
            help="Enable HTTPS and apply nginx configuration.",
        )
        action_group.add_argument(
            "--disable",
            action="store_true",
            help="Disable HTTPS and apply nginx configuration.",
        )
        action_group.add_argument(
            "--renew",
            action="store_true",
            help="Renew all due HTTPS certificates.",
        )

        cert_group = parser.add_mutually_exclusive_group()
        cert_group.add_argument(
            "--local",
            action="store_true",
            help="Use a self-signed localhost certificate (default).",
        )
        cert_group.add_argument(
            "--certbot",
            metavar="DOMAIN",
            help="Use certbot for the specified domain.",
        )
        cert_group.add_argument(
            "--godaddy",
            metavar="DOMAIN",
            help="Use certbot DNS-01 with GoDaddy for the specified domain.",
        )

        parser.add_argument(
            "--no-reload",
            action="store_true",
            help="Skip nginx reload/restart after applying changes.",
        )
        parser.add_argument(
            "--no-sudo",
            action="store_true",
            help="Run certificate provisioning without sudo.",
        )
        parser.add_argument(
            "--transport",
            choices=["nginx", "daphne"],
            help="Select HTTPS transport backend.",
        )

    def handle(self, *args, **options):
        """Dispatch https command actions and normalize implicit enable behavior."""

        enable = options["enable"]
        disable = options["disable"]
        renew = options["renew"]
        certbot_domain = options["certbot"]
        godaddy_domain = options["godaddy"]
        use_local = options["local"] or not (certbot_domain or godaddy_domain)
        use_godaddy = bool(godaddy_domain)
        reload = not options["no_reload"]
        sudo = "" if options["no_sudo"] else "sudo"
        selected_transport = options.get("transport")

        if not enable and not disable and not renew and (certbot_domain or godaddy_domain):
            enable = True

        if not enable and not disable and not renew:
            if options["local"]:
                raise CommandError("Use --enable or --disable with certificate options.")
            self._render_report(sudo=sudo)
            return

        domain = "localhost" if use_local else (godaddy_domain or certbot_domain)

        if disable:
            self._disable_https(domain, reload=reload)
            return

        if renew:
            self._renew_due_certificates(sudo=sudo)
            return

        certificate = self._enable_https(
            domain,
            use_local=use_local,
            use_godaddy=use_godaddy,
            sudo=sudo,
            reload=reload,
            transport=selected_transport,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"HTTPS enabled for {domain} using {certificate.__class__.__name__}."
            )
        )

    def _enable_https(
        self,
        domain: str,
        *,
        use_local: bool,
        use_godaddy: bool,
        sudo: str,
        reload: bool,
        transport: str | None,
    ):
        config = self._get_or_create_config(domain, protocol="https", transport=transport)
        certificate = self._get_or_create_certificate(
            domain,
            config,
            use_local=use_local,
            use_godaddy=use_godaddy,
        )

        update_fields: list[str] = []
        if config.certificate_id != certificate.id:
            config.certificate = certificate
            update_fields.append("certificate")

        config.sync_tls_paths_from_certificate()
        update_fields.extend(["tls_certificate_path", "tls_certificate_key_path"])
        config.save(update_fields=list(dict.fromkeys(update_fields)))

        if use_local:
            certificate.generate(
                sudo=sudo,
                subject_alt_names=["localhost", "127.0.0.1", "::1"],
            )
        else:
            if use_godaddy:
                self._validate_godaddy_setup(certificate)
            certificate.provision(sudo=sudo)

        self._apply_config(config, reload=reload)
        return certificate

    def _disable_https(self, domain: str, *, reload: bool) -> None:
        config = self._get_existing_config(domain)
        if config is None:
            raise CommandError(f"No site configuration found for {domain}.")

        update_fields: list[str] = []
        if config.protocol != "http":
            config.protocol = "http"
            update_fields.append("protocol")
        if config.transport != "nginx":
            config.transport = "nginx"
            update_fields.append("transport")
        if update_fields:
            config.save(update_fields=update_fields)

        self._apply_config(config, reload=reload)

    def _get_existing_config(self, domain: str) -> SiteConfiguration | None:
        name = "localhost" if domain == "localhost" else domain
        return SiteConfiguration.objects.filter(name=name).first()

    def _get_or_create_config(
        self,
        domain: str,
        *,
        protocol: str,
        transport: str | None = None,
    ) -> SiteConfiguration:
        defaults_source = SiteConfiguration.get_default()
        name = "localhost" if domain == "localhost" else domain
        config, created = SiteConfiguration.objects.get_or_create(name=name)
        if created:
            config.enabled = True
            config.protocol = protocol
            config.mode = defaults_source.mode
            config.role = defaults_source.role
            config.port = defaults_source.port
            config.include_ipv6 = defaults_source.include_ipv6
            config.external_websockets = defaults_source.external_websockets
            config.site_entries_path = defaults_source.site_entries_path
            config.site_destination = defaults_source.site_destination
            config.expected_path = defaults_source.expected_path
            config.transport = transport or defaults_source.transport
            config.save()
        else:
            update_fields: list[str] = []
            if config.protocol != protocol:
                config.protocol = protocol
                update_fields.append("protocol")
            if not config.enabled:
                config.enabled = True
                update_fields.append("enabled")
            if transport and config.transport != transport:
                config.transport = transport
                update_fields.append("transport")
            if update_fields:
                config.save(update_fields=update_fields)
        return config

    def _get_or_create_certificate(
        self,
        domain: str,
        config: SiteConfiguration,
        *,
        use_local: bool,
        use_godaddy: bool,
    ):
        slug = slugify(domain)
        if use_local:
            base_path = (
                Path(settings.BASE_DIR)
                / "scripts"
                / "generated"
                / "certificates"
                / slug
            )
            defaults = {
                "domain": domain,
                "certificate_path": str(base_path / "fullchain.pem"),
                "certificate_key_path": str(base_path / "privkey.pem"),
            }
            certificate, _ = SelfSignedCertificate.objects.update_or_create(
                name="local-https-localhost",
                defaults=defaults,
            )
        else:
            defaults = {
                "domain": domain,
                "certificate_path": f"/etc/letsencrypt/live/{domain}/fullchain.pem",
                "certificate_key_path": f"/etc/letsencrypt/live/{domain}/privkey.pem",
                "challenge_type": (
                    CertbotCertificate.ChallengeType.GODADDY
                    if use_godaddy
                    else CertbotCertificate.ChallengeType.NGINX
                ),
            }
            certificate, _ = CertbotCertificate.objects.update_or_create(
                name=f"{config.name or 'nginx-site'}-{slug}-certbot",
                defaults=defaults,
            )

        return certificate

    def _validate_godaddy_setup(self, certificate) -> None:
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
            credential = self._prompt_for_godaddy_credential(certbot.domain)
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

    def _prompt_for_godaddy_credential(self, domain: str) -> DNSProviderCredential | None:
        """Prompt for GoDaddy credentials and persist them for DNS-01 validation."""

        if not sys.stdin.isatty():
            self.stdout.write(
                "No enabled GoDaddy DNS credential was found."
            )
            self.stdout.write(
                "Create one in admin (DNS > DNS Credentials) or re-run this command in an interactive terminal to enter credentials now."
            )
            return None

        self.stdout.write(
            "No enabled GoDaddy DNS credential was found."
        )
        self.stdout.write(
            "Create API credentials in GoDaddy: Developer Portal -> API Keys -> Create New Key."
        )
        self.stdout.write(
            "Docs: https://developer.godaddy.com/keys"
        )
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
        self.stdout.write(
            self.style.SUCCESS("Saved GoDaddy DNS credential for automated DNS validation.")
        )
        return credential

    def _apply_config(self, config: SiteConfiguration, *, reload: bool) -> None:
        if config.transport == "daphne":
            self.stdout.write(
                self.style.SUCCESS(
                    "HTTPS active via Daphne direct TLS, nginx bypassed."
                )
            )
            return

        try:
            result = config.apply(reload=reload)
        except (NginxUnavailableError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(result.message))
        if not result.validated:
            self.stdout.write(
                "nginx configuration applied but validation was skipped or failed."
            )
        if not result.reloaded:
            self.stdout.write(
                "nginx reload/start did not complete automatically; check the service status."
            )

    def _render_report(self, *, sudo: str) -> None:
        configs = list(SiteConfiguration.objects.order_by("pk"))
        if not configs:
            self.stdout.write("No site configurations found.")
            return

        self.stdout.write("HTTPS status report:")
        for config in configs:
            cert = config.certificate
            cert_label = "none"
            cert_summary = ""
            if cert:
                cert_label = f"{cert.name} ({cert.__class__.__name__})"
                cert_summary = self._verify_certificate(cert, sudo=sudo)
            transport_summary = f"transport={config.transport}"
            if config.is_direct_tls_enabled:
                transport_summary += " (HTTPS active via Daphne direct TLS, nginx bypassed)"
            self.stdout.write(
                f"- {config.name}: protocol={config.protocol}, enabled={config.enabled}, "
                f"{transport_summary}, certificate={cert_label}"
            )
            if cert_summary:
                self.stdout.write(f"  - {cert_summary}")

    def _verify_certificate(self, cert, *, sudo: str) -> str:
        result = cert.verify(sudo=sudo)
        return self._format_verification_result(result)

    def _format_verification_result(self, result: CertificateVerificationResult) -> str:
        status = "valid" if result.ok else "invalid"
        summary = result.summary
        return f"Certificate status: {status}. {summary}"

    def _renew_due_certificates(self, *, sudo: str) -> None:
        now = timezone.now()
        due_certificates = CertificateBase.objects.filter(
            expiration_date__lte=now
        ).select_related("certbotcertificate", "selfsignedcertificate")

        if not due_certificates.exists():
            if CertificateBase.objects.filter(expiration_date__isnull=False).exists():
                self.stdout.write("No certificates were due for renewal.")
            else:
                self.stdout.write("No certificates are tracked for renewal.")
            return

        renewed = 0
        errors: list[str] = []
        for certificate in due_certificates:
            try:
                certificate.renew(sudo=sudo)
            except Exception as exc:
                errors.append(f"{certificate}: {exc}")
                continue
            renewed += 1

        if renewed:
            self.stdout.write(self.style.SUCCESS(f"Renewed {renewed} certificate(s)."))

        if errors:
            raise CommandError("Certificate renewal failed:\n" + "\n".join(errors))
        if not renewed:
            self.stdout.write("No certificates were due for renewal.")
