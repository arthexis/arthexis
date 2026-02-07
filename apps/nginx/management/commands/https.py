from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.certs.models import CertbotCertificate, SelfSignedCertificate
from apps.certs.services import CertificateVerificationResult
from apps.nginx.config_utils import slugify
from apps.nginx.models import SiteConfiguration
from apps.nginx.services import NginxUnavailableError, ValidationError


class Command(BaseCommand):
    help = "Manage HTTPS certificates and nginx configuration."  # noqa: A003

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
        certbot_domain = options["certbot"]
        use_local = options["local"] or not certbot_domain
        reload = not options["no_reload"]
        sudo = "" if options["no_sudo"] else "sudo"

        if not enable and not disable:
            if options["local"] or certbot_domain:
                raise CommandError("Use --enable or --disable with certificate options.")
            self._render_report(sudo=sudo)
            return

        domain = "localhost" if use_local else certbot_domain
        if not domain:
            raise CommandError("--certbot requires a domain.")

        if disable:
            self._disable_https(domain, reload=reload)
            return

        certificate = self._enable_https(
            domain,
            use_local=use_local,
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
        sudo: str,
        reload: bool,
    ):
        config = self._get_or_create_config(domain, protocol="https")
        certificate = self._get_or_create_certificate(domain, config, use_local=use_local)

        if config.certificate_id != certificate.id:
            config.certificate = certificate
            config.save(update_fields=["certificate"])

        if use_local:
            certificate.generate(
                sudo=sudo,
                subject_alt_names=["localhost", "127.0.0.1", "::1"],
            )
        else:
            certificate.provision(sudo=sudo)

        try:
            result = config.apply(reload=reload)
        except (NginxUnavailableError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(result.message))
        if not result.validated:
            self.stdout.write("nginx configuration applied but validation was skipped or failed.")
        if not result.reloaded:
            self.stdout.write(
                "nginx reload/start did not complete automatically; check the service status."
            )
        return certificate

    def _disable_https(self, domain: str, *, reload: bool) -> None:
        config = self._get_existing_config(domain)
        if config is None:
            raise CommandError(f"No site configuration found for {domain}.")

        if config.protocol != "http":
            config.protocol = "http"
            config.save(update_fields=["protocol"])

        try:
            result = config.apply(reload=reload)
        except (NginxUnavailableError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(result.message))
        if not result.validated:
            self.stdout.write("nginx configuration applied but validation was skipped or failed.")
        if not result.reloaded:
            self.stdout.write(
                "nginx reload/start did not complete automatically; check the service status."
            )

    def _get_existing_config(self, domain: str) -> SiteConfiguration | None:
        name = "localhost" if domain == "localhost" else domain
        return SiteConfiguration.objects.filter(name=name).first()

    def _get_or_create_config(self, domain: str, *, protocol: str) -> SiteConfiguration:
        defaults_source = SiteConfiguration.get_default()
        defaults = {
            "mode": defaults_source.mode,
            "role": defaults_source.role,
            "port": defaults_source.port,
            "include_ipv6": defaults_source.include_ipv6,
            "external_websockets": defaults_source.external_websockets,
            "site_entries_path": defaults_source.site_entries_path,
            "site_destination": defaults_source.site_destination,
            "expected_path": defaults_source.expected_path,
        }
        name = "localhost" if domain == "localhost" else domain
        config, created = SiteConfiguration.objects.get_or_create(name=name, defaults=defaults)

        desired = {
            "enabled": True,
            "protocol": protocol,
            "mode": defaults_source.mode,
            "role": defaults_source.role,
            "port": defaults_source.port,
            "include_ipv6": defaults_source.include_ipv6,
            "external_websockets": defaults_source.external_websockets,
            "site_entries_path": defaults_source.site_entries_path,
            "site_destination": defaults_source.site_destination,
            "expected_path": defaults_source.expected_path,
        }

        updated_fields: list[str] = []
        for field, value in desired.items():
            if getattr(config, field) != value:
                setattr(config, field, value)
                updated_fields.append(field)

        if created or updated_fields:
            config.save(update_fields=updated_fields or None)

        return config

    def _get_or_create_certificate(
        self,
        domain: str,
        config: SiteConfiguration,
        *,
        use_local: bool,
    ):
        if use_local:
            slug = slugify(domain)
            base_path = Path(settings.BASE_DIR) / "scripts" / "generated" / "certificates" / slug
            defaults = {
                "domain": domain,
                "certificate_path": str(base_path / "fullchain.pem"),
                "certificate_key_path": str(base_path / "privkey.pem"),
            }

            certificate, created = SelfSignedCertificate.objects.get_or_create(
                name="local-https-localhost",
                defaults=defaults,
            )
        else:
            slug = slugify(domain)
            defaults = {
                "domain": domain,
                "certificate_path": f"/etc/letsencrypt/live/{domain}/fullchain.pem",
                "certificate_key_path": f"/etc/letsencrypt/live/{domain}/privkey.pem",
            }

            certificate, created = CertbotCertificate.objects.get_or_create(
                name=f"{config.name or 'nginx-site'}-{slug}-certbot",
                defaults=defaults,
            )

        if not created:
            updated_fields: list[str] = []
            for field, value in defaults.items():
                if getattr(certificate, field) != value:
                    setattr(certificate, field, value)
                    updated_fields.append(field)
            if updated_fields:
                certificate.save(update_fields=updated_fields)

        return certificate

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
        try:
            result = cert.verify(sudo=sudo)
        except Exception as exc:  # pragma: no cover - depends on system
            return f"Verification failed: {exc}"
        return self._format_verification_result(result)

    def _format_verification_result(self, result: CertificateVerificationResult) -> str:
        status = "valid" if result.ok else "invalid"
        summary = result.summary
        return f"Certificate status: {status}. {summary}"
