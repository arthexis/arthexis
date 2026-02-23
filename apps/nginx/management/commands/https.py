from __future__ import annotations

from getpass import getpass
import ipaddress
from pathlib import Path
import shlex
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from django.utils import timezone
from datetime import timedelta
from django.contrib.sites.models import Site

from apps.sites.site_config import update_local_nginx_scripts
from config.settings_helpers import normalize_site_host

from apps.certs.models import CertificateBase, CertbotCertificate, SelfSignedCertificate
from apps.dns.models import DNSProviderCredential
from apps.certs.services import CertificateVerificationResult, CertbotChallengeError
from apps.nginx.config_utils import slugify
from apps.nginx.models import SiteConfiguration
from apps.nginx.services import NginxUnavailableError, ValidationError


FORCE_RENEWAL_EXPIRATION_UNAVAILABLE_WARNING = (
    "--force-renewal completed but certificate expiration could not be determined for {domain}. "
    "CertbotCertificate.request may have continued after services.get_certificate_expiration failed. "
    "Inspect certbot logs and DNS challenge status, then verify the certificate manually."
)
FORCE_RENEWAL_STILL_EXPIRED_ERROR = (
    "--force-renewal completed but the certificate is still expired. "
    "Inspect certbot logs and DNS challenge status, then retry."
)
CERTBOT_HTTP01_BOOTSTRAP_MESSAGE = (
    "The HTTP-01 challenge requires an active nginx site entry for this domain. "
    "Arthexis attempted to stage and apply an HTTP site configuration automatically before requesting the certificate."
)
NGINX_CONFIGURE_REMEDIATION_TEMPLATE = (
    "If nginx is not managed on this node yet, run '{command} nginx-configure' to bootstrap it, "
    "then re-run this https command."
)


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

        sandbox_group = parser.add_mutually_exclusive_group()
        sandbox_group.add_argument(
            "--sandbox",
            action="store_true",
            help="Force GoDaddy DNS requests to use the OTE sandbox API for this run.",
        )
        sandbox_group.add_argument(
            "--no-sandbox",
            action="store_true",
            help="Force GoDaddy DNS requests to use the production API for this run.",
        )

        parser.add_argument(
            "--site",
            metavar="HOST_OR_URL",
            help=(
                "Target host or URL to enable (for example, porsche.example.com or "
                "wss://porsche.example.com/)."
            ),
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
            "--force-renewal",
            action="store_true",
            help="Force certbot to issue a fresh certificate even if one already exists.",
        )
        parser.add_argument(
            "--warn-days",
            type=int,
            default=14,
            help="Warn when certificate expiration is within this many days (default: 14).",
        )

    def handle(self, *args, **options):
        """Dispatch https command actions and normalize implicit enable behavior."""

        enable = options["enable"]
        disable = options["disable"]
        renew = options["renew"]
        certbot_domain = options["certbot"]
        godaddy_domain = options["godaddy"]
        explicit_site = options["site"]
        parsed_site = self._parse_site_domain(explicit_site) if explicit_site else None

        if parsed_site and options["local"]:
            raise CommandError("--local cannot be combined with --site. Use --certbot/--godaddy or omit --local.")

        certbot_domain = certbot_domain or (parsed_site if parsed_site and not godaddy_domain else None)
        certbot_domain = self._parse_site_domain(certbot_domain) if certbot_domain else None
        godaddy_domain = self._parse_site_domain(godaddy_domain) if godaddy_domain else None
        use_local = options["local"] or not (certbot_domain or godaddy_domain)
        use_godaddy = bool(godaddy_domain)
        sandbox_override = self._parse_sandbox_override(options)
        reload = not options["no_reload"]
        sudo = "" if options["no_sudo"] else "sudo"
        force_renewal = options["force_renewal"]
        warn_days = options["warn_days"]

        if warn_days < 0:
            raise CommandError("--warn-days must be zero or a positive integer.")

        if use_local and force_renewal:
            raise CommandError("--force-renewal is only supported for certbot/godaddy certificates.")

        if not enable and not disable and not renew and (certbot_domain or godaddy_domain or parsed_site):
            enable = True

        if not enable and not disable and not renew:
            if options["local"]:
                raise CommandError("Use --enable or --disable with certificate options.")
            self._render_report(sudo=sudo)
            return

        domain = "localhost" if use_local else (godaddy_domain or certbot_domain)
        if not domain:
            raise CommandError("No target domain was provided. Use --site, --certbot, --godaddy, or --local.")

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
            sandbox_override=sandbox_override,
            sudo=sudo,
            reload=reload,
            force_renewal=force_renewal,
            warn_days=warn_days,
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
        sandbox_override: bool | None,
        sudo: str,
        reload: bool,
        force_renewal: bool,
        warn_days: int,
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
            http01_bootstrapped = False
            if force_renewal:
                previous_certificate_path = certificate.certificate_path
                previous_certificate_key_path = certificate.certificate_key_path
            try:
                if not use_godaddy:
                    self._prepare_http01_challenge_site(domain, reload=reload)
                    http01_bootstrapped = True
                if use_godaddy:
                    self._validate_godaddy_setup(certificate)
                certificate.provision(
                    sudo=sudo,
                    dns_use_sandbox=sandbox_override,
                    force_renewal=force_renewal,
                )
            except CertbotChallengeError as exc:
                if http01_bootstrapped:
                    self._restore_https_config_after_http01_bootstrap(config, reload=reload)
                raise CommandError(
                    self._build_certbot_challenge_command_error(
                        domain=domain,
                        challenge_type=certificate.challenge_type,
                        reason=str(exc),
                    )
                ) from exc
            if force_renewal:
                self._warn_if_certificate_paths_changed(
                    certificate,
                    previous_certificate_path=previous_certificate_path,
                    previous_certificate_key_path=previous_certificate_key_path,
                )
                self._validate_force_renewal_result(certificate)

        self._warn_if_certificate_expiring_soon(certificate, warn_days=warn_days)
        SiteConfiguration.objects.filter(pk=config.pk).update(protocol="https", enabled=True)
        config.refresh_from_db(fields=["protocol", "enabled"])
        self._ensure_managed_site(domain, require_https=True)
        self._apply_config(config, reload=reload)
        return certificate

    def _prepare_http01_challenge_site(self, domain: str, *, reload: bool) -> None:
        """Ensure nginx can answer HTTP-01 challenges before certbot provisioning."""

        self._ensure_managed_site(domain, require_https=False)
        bootstrap_config = self._get_or_create_config(domain, protocol="http")
        self._apply_config(bootstrap_config, reload=reload)

    def _restore_https_config_after_http01_bootstrap(
        self,
        config: SiteConfiguration,
        *,
        reload: bool,
    ) -> None:
        """Restore the persisted/runtime site protocol to HTTPS after HTTP-01 bootstrap."""

        SiteConfiguration.objects.filter(pk=config.pk).update(protocol="https", enabled=True)
        config.refresh_from_db(fields=["protocol", "enabled"])
        self._apply_config(config, reload=reload)

    def _build_certbot_challenge_command_error(
        self,
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

    def _validate_force_renewal_result(self, certificate) -> None:
        """Raise when force-renewal returns but the certificate is still expired."""

        expiration = getattr(certificate, "expiration_date", None)
        if expiration is None:
            domain = getattr(certificate, "domain", "the requested domain")
            self.stdout.write(
                self.style.WARNING(
                    FORCE_RENEWAL_EXPIRATION_UNAVAILABLE_WARNING.format(domain=domain)
                )
            )
            return

        if expiration <= timezone.now():
            raise CommandError(FORCE_RENEWAL_STILL_EXPIRED_ERROR)

    def _warn_if_certificate_paths_changed(
        self,
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

        self.stdout.write(
            self.style.WARNING(
                "Certificate storage paths changed after certbot issuance: "
                f"cert old={previous_certificate_path} new={certificate.certificate_path}; "
                f"key old={previous_certificate_key_path} new={certificate.certificate_key_path}. "
                "If external services reference old paths, reload or update them accordingly."
            )
        )

    def _warn_if_certificate_expiring_soon(self, certificate, *, warn_days: int) -> None:
        """Emit actionable guidance when certificate expiration is near or in the past."""

        expiration = getattr(certificate, "expiration_date", None)
        if expiration is None:
            return

        now = timezone.now()
        threshold = now + timedelta(days=warn_days)
        if expiration <= threshold:
            is_certbot = isinstance(certificate, CertbotCertificate)
            quoted_domain = shlex.quote(certificate.domain)

            if expiration <= now:
                status = "has expired"
                remediation = "Run './command.sh https --renew' to reissue due certificates."

                if is_certbot:
                    remediation += (
                        " Use './command.sh https --enable --force-renewal "
                        f"--certbot {quoted_domain}' (or '--godaddy {quoted_domain}') "
                        "only when you need to force immediate reissuance."
                    )
            else:
                status = "expires soon"
                if is_certbot:
                    remediation = (
                        "Run './command.sh https --enable --force-renewal "
                        f"--certbot {quoted_domain}' (or '--godaddy {quoted_domain}') to reissue immediately."
                    )
                else:
                    remediation = "Run './command.sh https --enable --local' to reissue immediately."

            self.stdout.write(
                self.style.WARNING(
                    f"Certificate for {certificate.domain} {status} at {expiration.isoformat()}. "
                    f"{remediation}"
                )
            )

    def _parse_site_domain(self, candidate: str | None) -> str | None:
        """Return a normalized host parsed from ``--site`` input."""

        normalized = normalize_site_host(candidate or "")
        if not normalized:
            raise CommandError("--site must include a valid hostname or URL.")

        if normalized == "localhost":
            raise CommandError("--site requires a public host. Use --local for local development.")

        if normalized.startswith("-"):
            raise CommandError("--site must include a valid hostname or URL.")

        try:
            parsed_ip = ipaddress.ip_address(normalized)
        except ValueError:
            parsed_ip = None

        if parsed_ip is not None and parsed_ip.is_loopback:
            raise CommandError("--site requires a public host. Use --local for local development.")

        return normalized

    def _ensure_managed_site(self, domain: str, *, require_https: bool) -> None:
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

    def _parse_sandbox_override(self, options: dict[str, object]) -> bool | None:
        """Return a per-run GoDaddy sandbox override derived from CLI options."""

        if options["sandbox"]:
            return True
        if options["no_sandbox"]:
            return False
        return None

    def _disable_https(self, domain: str, *, reload: bool) -> None:
        config = self._get_existing_config(domain)
        if config is None:
            raise CommandError(f"No site configuration found for {domain}.")

        if config.protocol != "http":
            config.protocol = "http"
            config.save(update_fields=["protocol"])

        self._apply_config(config, reload=reload)
        self._ensure_managed_site(domain, require_https=False)

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
                # Preserve existing certbot lineage paths when present.
                if not certificate.certificate_path:
                    certificate.certificate_path = (
                        f"/etc/letsencrypt/live/{domain}/fullchain.pem"
                    )
                    updated_fields.append("certificate_path")
                if not certificate.certificate_key_path:
                    certificate.certificate_key_path = (
                        f"/etc/letsencrypt/live/{domain}/privkey.pem"
                    )
                    updated_fields.append("certificate_key_path")
                if updated_fields:
                    certificate.save(update_fields=[*updated_fields, "updated_at"])

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
        try:
            result = config.apply(reload=reload)
        except (NginxUnavailableError, ValidationError) as exc:
            raise CommandError(
                f"{exc}\n" +
                NGINX_CONFIGURE_REMEDIATION_TEMPLATE.format(command=sys.argv[0])
            ) from exc

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
