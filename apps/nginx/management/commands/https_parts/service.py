"""Service object coordinating HTTPS command operations."""

from __future__ import annotations

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction

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
    _parse_site_domain,
)
from apps.nginx.management.commands.https_parts.renewal import _renew_due_certificates
from apps.nginx.management.commands.https_parts.reporting import _render_report
from apps.nginx.management.commands.https_parts.verification import (
    _warn_if_certificate_expiring_soon,
)
from apps.nginx.models import SiteConfiguration
from apps.nodes.models import Node
from apps.sites.models import SiteProfile
from apps.sites.site_config import update_local_nginx_scripts


class HttpsProvisioningService:
    """Encapsulate HTTPS command state and operational workflows."""

    _MIGRATABLE_SITE_CONFIG_FIELDS = (
        "enabled",
        "mode",
        "role",
        "port",
        "external_websockets",
        "managed_subdomains",
        "include_ipv6",
        "expected_path",
        "site_entries_path",
        "site_destination",
    )

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
        positional_domain = options.get("domain")
        explicit_migrate_from = options.get("migrate_from")

        if positional_domain and not (
            certbot_domain or godaddy_domain or explicit_site
        ):
            certbot_domain = positional_domain

        parsed_site = _parse_site_domain(explicit_site) if explicit_site else None
        migrate_from = (
            _parse_site_domain(explicit_migrate_from) if explicit_migrate_from else None
        )

        if (
            migrate_from
            and not parsed_site
            and not certbot_domain
            and not godaddy_domain
        ):
            raise CommandError(
                "--migrate-from requires a target domain via --site, --certbot, or --godaddy."
            )

        if migrate_from and (disable or renew or validate):
            raise CommandError("--migrate-from is only supported when enabling HTTPS.")

        if parsed_site and options["local"]:
            raise CommandError(
                "--local cannot be combined with --site. Use a public-domain flag or omit --local."
            )

        if positional_domain and options["local"]:
            raise CommandError(
                "Positional domain cannot be combined with --local. Use --certbot/--godaddy/--site or omit domain."
            )

        certbot_domain = certbot_domain or (
            parsed_site if parsed_site and not godaddy_domain else None
        )
        certbot_domain = _parse_site_domain(certbot_domain) if certbot_domain else None
        godaddy_domain = _parse_site_domain(godaddy_domain) if godaddy_domain else None
        use_local = options["local"] or not (certbot_domain or godaddy_domain)
        reload = not options["no_reload"]
        sudo = "" if options["no_sudo"] else "sudo"
        force_renewal = options["force_renewal"]
        warn_days = options["warn_days"]

        if godaddy_domain:
            raise CommandError(
                "Automated GoDaddy DNS setup was removed. Configure DNS records manually, "
                "then run HTTPS enable with --certbot DOMAIN (or --site HOST_OR_URL) to keep nginx managed config."
            )
        if options.get("sandbox") or options.get("no_sandbox"):
            raise CommandError(
                "--sandbox/--no-sandbox are no longer supported. "
                "Use manual DNS configuration, then run HTTPS with --certbot or --site."
            )
        if (options.get("key") or "").strip() or (
            options.get("static_ip") or ""
        ).strip():
            raise CommandError(
                "--key/--static-ip are no longer supported. "
                "Use manual DNS configuration, then run HTTPS with --certbot or --site."
            )

        if warn_days < 0:
            raise CommandError("--warn-days must be zero or a positive integer.")

        if use_local and force_renewal:
            raise CommandError(
                "--force-renewal is only supported for certbot certificates."
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

        if migrate_from:
            with transaction.atomic():
                migration_source_config = self._migrate_domain_records(
                    source_domain=migrate_from,
                    target_domain=domain,
                )
                certificate = self._enable_https(
                    domain,
                    use_local=use_local,
                    sudo=sudo,
                    reload=reload,
                    force_renewal=force_renewal,
                    warn_days=warn_days,
                    migrate_from_config=migration_source_config,
                )
                transaction.on_commit(update_local_nginx_scripts)
        else:
            certificate = self._enable_https(
                domain,
                use_local=use_local,
                sudo=sudo,
                reload=reload,
                force_renewal=force_renewal,
                warn_days=warn_days,
            )
            transaction.on_commit(update_local_nginx_scripts)

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
        force_renewal: bool,
        warn_days: int,
        migrate_from_config: SiteConfiguration | None = None,
    ):
        """Enable HTTPS for a target domain and apply the resulting nginx state.

        Parameters:
            domain: Destination hostname being enabled for HTTPS.
            use_local: Whether to issue a local/self-signed certificate flow.
            sudo: Prefix used for shell commands requiring privileged access.
            reload: Whether nginx should be reloaded after config changes.
            force_renewal: Whether certificate issuance should force renewal.
            warn_days: Threshold in days to warn if certificate expiry is near.
            migrate_from_config: Optional source configuration to copy during migration.

        Returns:
            The created or reused certificate instance bound to the HTTPS config.

        Raises:
            CommandError: Propagated when certificate provisioning or config apply fails.
        """

        config = _get_or_create_config(domain, protocol="https")
        if migrate_from_config is not None:
            self._copy_site_configuration(source=migrate_from_config, target=config)
        certificate = _get_or_create_certificate(
            domain,
            config,
            use_local=use_local,
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

        default_site_id = getattr(settings, "SITE_ID", None)
        if isinstance(default_site_id, int) and default_site_id > 0:
            site = Site.objects.filter(pk=default_site_id).first()
            created = site is None
            if site is None:
                site = Site(pk=default_site_id, domain=domain, name=domain)
                try:
                    with transaction.atomic():
                        site.save(force_insert=True)
                except IntegrityError:
                    fallback_site = Site.objects.filter(domain=domain).first()
                    if fallback_site is None:
                        raise
                    raise CommandError(
                        "Cannot set the configured SITE_ID as the default site because "
                        f"domain '{domain}' already belongs to Site {fallback_site.pk}. "
                        "Resolve the duplicate site records, then run HTTPS setup again."
                    )
        else:
            site, created = Site.objects.get_or_create(
                domain=domain, defaults={"name": domain}
            )
        updated_fields: list[str] = []

        if site.domain != domain:
            site.domain = domain
            updated_fields.append("domain")
        if site.name != domain:
            site.name = domain
            updated_fields.append("name")
        profile, _profile_created = SiteProfile.objects.get_or_create(site=site)
        profile_updates: list[str] = []
        if not profile.managed:
            profile.managed = True
            profile_updates.append("managed")
        if profile.require_https != require_https:
            profile.require_https = require_https
            profile_updates.append("require_https")
        if created:
            with transaction.atomic():
                if updated_fields:
                    site.save(update_fields=updated_fields)
                if profile_updates:
                    profile.save(update_fields=profile_updates)
        elif updated_fields:
            try:
                with transaction.atomic():
                    site.save(update_fields=updated_fields)
                    if profile_updates:
                        profile.save(update_fields=profile_updates)
            except IntegrityError:
                fallback_site = Site.objects.filter(domain=domain).first()
                if fallback_site is not None:
                    if site.pk != fallback_site.pk:
                        raise CommandError(
                            f"Configured SITE_ID ({site.pk}) could not be updated to domain "
                            f"'{domain}' because that domain already belongs to Site {fallback_site.pk}. "
                            "Resolve the duplicate site records, then run HTTPS setup again."
                        )
                    fallback_updates: list[str] = []
                    if fallback_site.name != domain:
                        fallback_site.name = domain
                        fallback_updates.append("name")
                    if fallback_updates:
                        fallback_site.save(update_fields=fallback_updates)
                    fallback_profile, _created = SiteProfile.objects.get_or_create(
                        site=fallback_site
                    )
                    fallback_profile_updates: list[str] = []
                    if not fallback_profile.managed:
                        fallback_profile.managed = True
                        fallback_profile_updates.append("managed")
                    if fallback_profile.require_https != require_https:
                        fallback_profile.require_https = require_https
                        fallback_profile_updates.append("require_https")
                    if fallback_profile_updates:
                        fallback_profile.save(update_fields=fallback_profile_updates)
        elif profile_updates:
            with transaction.atomic():
                profile.save(update_fields=profile_updates)

    def _migrate_domain_records(
        self,
        *,
        source_domain: str,
        target_domain: str,
    ) -> SiteConfiguration | None:
        """Migrate persisted Site/Node references from one domain to another.

        Parameters:
            source_domain: Existing domain whose records should be migrated.
            target_domain: Destination domain receiving migrated references.

        Returns:
            The source SiteConfiguration when it exists, otherwise ``None``.

        Raises:
            CommandError: If source and target are identical, or if target conflicts.
        """

        if source_domain == target_domain:
            raise CommandError(
                "--migrate-from source must differ from the target domain."
            )

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
        if (
            source_config
            and source_config.enabled
            and source_config.protocol == "https"
        ):
            source_config.enabled = False
            source_config.save(update_fields=["enabled"])

        if source_config or source_site:
            self.stdout.write(
                self.style.WARNING(
                    f"Migrating domain records from {source_domain} to {target_domain}; {migrated_nodes} node hostname(s) updated."
                )
            )
        return source_config

    @staticmethod
    def _copy_site_configuration(
        *, source: SiteConfiguration, target: SiteConfiguration
    ) -> None:
        """Copy migratable runtime fields from a source config to a target config.

        Parameters:
            source: Existing site configuration used as migration source.
            target: Destination configuration to mutate with migrated values.

        Returns:
            ``None``. The target instance is updated in place and saved when changed.

        Raises:
            CommandError: Not raised directly by this helper.
        """

        updated_fields: list[str] = []
        for field_name in HttpsProvisioningService._MIGRATABLE_SITE_CONFIG_FIELDS:
            source_value = getattr(source, field_name)
            if getattr(target, field_name) != source_value:
                setattr(target, field_name, source_value)
                updated_fields.append(field_name)

        if updated_fields:
            target.save(update_fields=updated_fields)
