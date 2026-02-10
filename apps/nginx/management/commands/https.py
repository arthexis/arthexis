from __future__ import annotations

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

    def handle(self, *args, **options):
        enable = options["enable"]
        disable = options["disable"]
        renew = options["renew"]
        certbot_domain = options["certbot"]
        godaddy_domain = options["godaddy"]
        use_local = options["local"] or not (certbot_domain or godaddy_domain)
        use_godaddy = bool(godaddy_domain)
        reload = not options["no_reload"]
        sudo = "" if options["no_sudo"] else "sudo"

        if not enable and not disable and not renew:
            if options["local"] or certbot_domain or godaddy_domain:
                raise CommandError(
                    "Use --enable or --disable with certificate options."
                )
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
    ):
        config = self._get_or_create_config(domain, protocol="https")
        certificate = self._get_or_create_certificate(
            domain,
            config,
            use_local=use_local,
            use_godaddy=use_godaddy,
        )

        if config.certificate_id != certificate.id:
            config.certificate = certificate
            config.save(update_fields=["certificate"])

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

        if config.protocol != "http":
            config.protocol = "http"
            config.save(update_fields=["protocol"])

        self._apply_config(config, reload=reload)

    def _get_existing_config(self, domain: str) -> SiteConfiguration | None:
        name = "localhost" if domain == "localhost" else domain
        return SiteConfiguration.objects.filter(name=name).first()

    def _get_or_create_config(self, domain: str, *, protocol: str) -> SiteConfiguration:
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
            config.save()
        else:
            if config.protocol != protocol or not config.enabled:
                config.protocol = protocol
                config.enabled = True
                config.save(update_fields=["protocol", "enabled"])
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
            raise CommandError(
                "GoDaddy DNS validation requires an enabled DNS credential in admin (DNS > DNS Credentials)."
            )
        certbot.dns_credential = credential
        certbot.save(update_fields=["dns_credential", "updated_at"])
        self.stdout.write(
            "Using GoDaddy credential '%s'. Ensure certbot and Python requests are available to run DNS hooks."
            % credential
        )

    def _apply_config(self, config: SiteConfiguration, *, reload: bool) -> None:
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
            self.stdout.write(
                f"- {config.name}: protocol={config.protocol}, enabled={config.enabled}, "
                f"certificate={cert_label}"
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
