from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.certs.models import SelfSignedCertificate
from apps.nginx.config_utils import slugify
from apps.nginx.models import SiteConfiguration
from apps.nginx.services import NginxUnavailableError, ValidationError


class Command(BaseCommand):
    help = "Provision a self-signed certificate and nginx config for https://localhost."  # noqa: A003

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-reload",
            action="store_true",
            help="Skip nginx reload/restart after applying changes.",
        )
        parser.add_argument(
            "--no-sudo",
            action="store_true",
            help="Generate the local certificate without sudo.",
        )

    def handle(self, *args, **options):
        local_config = self._get_or_create_local_config()
        certificate = self._get_or_create_local_certificate()
        sudo = "" if options["no_sudo"] else "sudo"

        certificate.generate(
            sudo=sudo,
            subject_alt_names=["localhost", "127.0.0.1", "::1"],
        )

        if local_config.certificate_id != certificate.id:
            local_config.certificate = certificate
            local_config.save(update_fields=["certificate"])

        reload = not options["no_reload"]

        try:
            result = local_config.apply(reload=reload)
        except NginxUnavailableError as exc:  # pragma: no cover - depends on system nginx
            raise CommandError(str(exc))
        except ValidationError as exc:
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS(result.message))
        if not result.validated:
            self.stdout.write("nginx configuration applied but validation was skipped or failed.")
        if not result.reloaded:
            self.stdout.write("nginx reload/start did not complete automatically; check the service status.")

    def _get_or_create_local_config(self) -> SiteConfiguration:
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
        local_config, created = SiteConfiguration.objects.get_or_create(
            name="localhost",
            defaults=defaults,
        )

        updated_fields: list[str] = []
        desired = {
            "enabled": True,
            "protocol": "https",
            "mode": defaults_source.mode,
            "role": defaults_source.role,
            "port": defaults_source.port,
            "include_ipv6": defaults_source.include_ipv6,
            "external_websockets": defaults_source.external_websockets,
            "site_entries_path": defaults_source.site_entries_path,
            "site_destination": defaults_source.site_destination,
            "expected_path": defaults_source.expected_path,
        }

        for field, value in desired.items():
            if getattr(local_config, field) != value:
                setattr(local_config, field, value)
                updated_fields.append(field)

        if created or updated_fields:
            local_config.save(update_fields=updated_fields or None)

        return local_config

    def _get_or_create_local_certificate(self) -> SelfSignedCertificate:
        domain = "localhost"
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

        updated_fields: list[str] = []
        if not created:
            for field, value in defaults.items():
                if getattr(certificate, field) != value:
                    setattr(certificate, field, value)
                    updated_fields.append(field)
            if updated_fields:
                certificate.save(update_fields=updated_fields)

        return certificate
