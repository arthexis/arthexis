"""Service object coordinating HTTPS command operations."""

from __future__ import annotations

from django.contrib.sites.models import Site
from django.core.management.base import CommandError

from apps.nodes.models import Node

from apps.nginx.management.commands.https_parts.certificate_flow import (
    _get_or_create_certificate,
    _provision_certificate,
)
from apps.nginx.management.commands.https_parts.config_apply import (
    _apply_config,
    _get_existing_config,
    _get_or_create_config,
)
from apps.nginx.management.commands.https_parts.parsing import (
    _parse_sandbox_override,
    _parse_site_domain,
)
from apps.nginx.management.commands.https_parts.reporting import _render_report
from apps.nginx.management.commands.https_parts.renewal import _renew_due_certificates
from apps.nginx.management.commands.https_parts.verification import (
    _warn_if_certificate_expiring_soon,
)
from apps.nginx.models import SiteConfiguration
from apps.sites.site_config import update_local_nginx_scripts


class HttpsProvisioningService:
    """Encapsulate HTTPS command state and operational workflows."""

    def __init__(self, command):
        """Initialize service from management command IO/style handles."""

        self.command = command
        self.stdout = command.stdout
        self.style = command.style

    def handle(self, options: dict[str, object]) -> None:
        """Process command options while preserving existing CLI behavior."""

        enable = options["enable"]
        disable = options["disable"]
        renew = options["renew"]
        validate = options["validate"]
        certbot_domain = options["certbot"]
        godaddy_domain = options["godaddy"]
        explicit_site = options["site"]
        explicit_migrate_from = options.get("migrate_from")
        parsed_site = _parse_site_domain(explicit_site) if explicit_site else None
        migrate_from = (
            _parse_site_domain(explicit_migrate_from)
            if explicit_migrate_from
            else None
        )

        if migrate_from and not parsed_site and not certbot_domain and not godaddy_domain:
            raise CommandError(
                "--migrate-from requires a target domain via --site, --certbot, or --godaddy."
            )

        if migrate_from and options["local"]:
            raise CommandError("--migrate-from cannot be combined with --local.")

        if parsed_site and options["local"]:
            raise CommandError(
                "--local cannot be combined with --site. Use --certbot/--godaddy or omit --local."
            )

        certbot_domain = certbot_domain or (
            parsed_site if parsed_site and not godaddy_domain else None
        )
        certbot_domain = _parse_site_domain(certbot_domain) if certbot_domain else None
        godaddy_domain = _parse_site_domain(godaddy_domain) if godaddy_domain else None
        use_local = options["local"] or not (certbot_domain or godaddy_domain)
        use_godaddy = bool(godaddy_domain)
        sandbox_override = _parse_sandbox_override(options)
        reload = not options["no_reload"]
        sudo = "" if options["no_sudo"] else "sudo"
        force_renewal = options["force_renewal"]
        warn_days = options["warn_days"]

        if warn_days < 0:
            raise CommandError("--warn-days must be zero or a positive integer.")

        if use_local and force_renewal:
            raise CommandError(
                "--force-renewal is only supported for certbot/godaddy certificates."
            )

        if (
            not enable
            and not disable
            and not renew
            and not validate
            and (certbot_domain or godaddy_domain or parsed_site)
        ):
            enable = True

        if validate:
            _render_report(
                self,
                sudo=sudo,
                domain_filter=godaddy_domain or certbot_domain or parsed_site,
                require_godaddy=bool(godaddy_domain),
                require_local=bool(options["local"]),
            )
            return

        if not enable and not disable and not renew:
            if options["local"]:
                raise CommandError(
                    "Use --enable, --disable, or --validate with certificate options."
                )
            _render_report(self, sudo=sudo)
            return

        domain = "localhost" if use_local else (godaddy_domain or certbot_domain)
        if not domain:
            raise CommandError(
                "No target domain was provided. Use --site, --certbot, --godaddy, or --local."
            )

        if migrate_from and domain == "localhost":
            raise CommandError("--migrate-from requires a public target domain.")

        if disable:
            self._disable_https(domain, reload=reload)
            return

        if renew:
            _renew_due_certificates(
                self,
                sudo=sudo,
                reload=reload,
                domain_filter=godaddy_domain or certbot_domain,
                require_godaddy=bool(godaddy_domain),
                require_local=bool(options["local"]),
            )
            return

        migration_source_config = None
        if migrate_from:
            migration_source_config = self._migrate_domain_records(
                source_domain=migrate_from,
                target_domain=domain,
            )

        certificate = self._enable_https(
            domain,
            use_local=use_local,
            use_godaddy=use_godaddy,
            sandbox_override=sandbox_override,
            sudo=sudo,
            reload=reload,
            force_renewal=force_renewal,
            warn_days=warn_days,
            migrate_from_config=migration_source_config,
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
        migrate_from_config: SiteConfiguration | None = None,
    ):
        """Enable HTTPS for a site, provision certs, and apply nginx configuration."""

        config = _get_or_create_config(domain, protocol="https")
        if migrate_from_config is not None:
            self._copy_site_configuration(source=migrate_from_config, target=config)
        certificate = _get_or_create_certificate(
            domain,
            config,
            use_local=use_local,
            use_godaddy=use_godaddy,
        )

        if config.certificate_id != certificate.id:
            config.certificate = certificate
            config.save(update_fields=["certificate"])

        _provision_certificate(
            self,
            domain=domain,
            config=config,
            certificate=certificate,
            use_local=use_local,
            use_godaddy=use_godaddy,
            sandbox_override=sandbox_override,
            sudo=sudo,
            reload=reload,
            force_renewal=force_renewal,
        )

        _warn_if_certificate_expiring_soon(self, certificate, warn_days=warn_days)
        SiteConfiguration.objects.filter(pk=config.pk).update(
            protocol="https", enabled=True
        )
        config.refresh_from_db(fields=["protocol", "enabled"])
        self._ensure_managed_site(domain, require_https=True)
        _apply_config(self, config, reload=reload)
        return certificate

    def _disable_https(self, domain: str, *, reload: bool) -> None:
        """Disable HTTPS on a site and apply the HTTP configuration."""

        config = _get_existing_config(domain)
        if config is None:
            raise CommandError(f"No site configuration found for {domain}.")

        if config.protocol != "http":
            config.protocol = "http"
            config.save(update_fields=["protocol"])

        _apply_config(self, config, reload=reload)
        self._ensure_managed_site(domain, require_https=False)

    def _ensure_managed_site(self, domain: str, *, require_https: bool) -> None:
        """Persist target domain as managed Site and refresh staged nginx hosts."""

        if domain == "localhost":
            return
        site, created = Site.objects.get_or_create(
            domain=domain, defaults={"name": domain}
        )
        updated_fields: list[str] = []

        if hasattr(site, "managed") and not getattr(site, "managed"):
            setattr(site, "managed", True)
            updated_fields.append("managed")
        if (
            hasattr(site, "require_https")
            and getattr(site, "require_https") != require_https
        ):
            setattr(site, "require_https", require_https)
            updated_fields.append("require_https")
        if created:
            site.save()
        elif updated_fields:
            site.save(update_fields=updated_fields)

        update_local_nginx_scripts()


    def _migrate_domain_records(
        self,
        *,
        source_domain: str,
        target_domain: str,
    ) -> SiteConfiguration | None:
        """Move local Site and Node domain references from one host to another."""

        if source_domain == target_domain:
            raise CommandError("--migrate-from source must differ from the target domain.")

        target_site = Site.objects.filter(domain__iexact=target_domain).first()
        source_site = Site.objects.filter(domain__iexact=source_domain).first()
        if source_site and target_site and source_site.pk != target_site.pk:
            raise CommandError(
                f"Cannot migrate from {source_domain}: target domain {target_domain} already exists as a different Site."
            )

        if source_site and not target_site:
            target_site = source_site
            target_site.domain = target_domain
            target_site.name = target_domain
            target_site.save(update_fields=["domain", "name"])
        elif not target_site:
            target_site = Site.objects.create(domain=target_domain, name=target_domain)

        if source_site and source_site.pk != target_site.pk:
            Node.objects.filter(base_site=source_site).update(base_site=target_site)

        migrated_nodes = Node.objects.filter(hostname__iexact=source_domain).update(
            hostname=target_domain
        )

        source_config = SiteConfiguration.objects.filter(name=source_domain).first()
        if source_config and source_config.name != target_domain:
            self.stdout.write(
                self.style.WARNING(
                    f"Migrating domain records from {source_domain} to {target_domain}; {migrated_nodes} node hostname(s) updated."
                )
            )
        elif source_site:
            self.stdout.write(
                self.style.WARNING(
                    f"Migrating domain records from {source_domain} to {target_domain}; {migrated_nodes} node hostname(s) updated."
                )
            )

        update_local_nginx_scripts()
        return source_config

    @staticmethod
    def _copy_site_configuration(
        *, source: SiteConfiguration, target: SiteConfiguration
    ) -> None:
        """Copy key runtime settings from an existing site config to a target config."""

        field_names = (
            "enabled",
            "mode",
            "port",
            "external_websockets",
            "managed_subdomains",
            "include_ipv6",
            "expected_path",
            "site_entries_path",
            "site_destination",
        )
        updated_fields: list[str] = []
        for field_name in field_names:
            source_value = getattr(source, field_name)
            if getattr(target, field_name) != source_value:
                setattr(target, field_name, source_value)
                updated_fields.append(field_name)

        if updated_fields:
            target.save(update_fields=updated_fields)
