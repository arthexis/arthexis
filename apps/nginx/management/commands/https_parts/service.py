"""Service object coordinating HTTPS command operations."""

from __future__ import annotations

import ipaddress

import requests
from django.contrib.sites.models import Site
from django.core.management.base import CommandError
from django.db import transaction

from apps.dns.models import DNSProviderCredential
from apps.nginx.management.commands.https_parts.certificate_flow import (
    _get_or_create_certificate,
    _provision_certificate,
    _resolve_godaddy_credential,
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
from apps.nginx.management.commands.https_parts.renewal import _renew_due_certificates
from apps.nginx.management.commands.https_parts.reporting import _render_report
from apps.nginx.management.commands.https_parts.verification import (
    _warn_if_certificate_expiring_soon,
)
from apps.nginx.models import SiteConfiguration
from apps.nodes.models import Node
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

        if positional_domain and not (certbot_domain or godaddy_domain or explicit_site):
            certbot_domain = positional_domain

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
        use_godaddy = bool(godaddy_domain)
        sandbox_override = _parse_sandbox_override(options)
        reload = not options["no_reload"]
        sudo = "" if options["no_sudo"] else "sudo"
        force_renewal = options["force_renewal"]
        godaddy_credential_key = (options.get("key") or "").strip() or None
        static_ip = self._parse_public_ip((options.get("static_ip") or "").strip())
        warn_days = options["warn_days"]

        if warn_days < 0:
            raise CommandError("--warn-days must be zero or a positive integer.")

        if use_local and force_renewal:
            raise CommandError(
                "--force-renewal is only supported for certbot/godaddy certificates."
            )
        if static_ip and not use_godaddy:
            raise CommandError("--static-ip is only supported with --godaddy.")

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
                    use_godaddy=use_godaddy,
                    sandbox_override=sandbox_override,
                    sudo=sudo,
                    reload=reload,
                    force_renewal=force_renewal,
                    godaddy_credential_key=godaddy_credential_key,
                    static_ip=static_ip,
                    warn_days=warn_days,
                    migrate_from_config=migration_source_config,
                )
                transaction.on_commit(update_local_nginx_scripts)
        else:
            certificate = self._enable_https(
                domain,
                use_local=use_local,
                use_godaddy=use_godaddy,
                sandbox_override=sandbox_override,
                sudo=sudo,
                reload=reload,
                force_renewal=force_renewal,
                godaddy_credential_key=godaddy_credential_key,
                static_ip=static_ip,
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
        use_godaddy: bool,
        sandbox_override: bool | None,
        sudo: str,
        reload: bool,
        force_renewal: bool,
        godaddy_credential_key: str | None,
        static_ip: str | None,
        warn_days: int,
        migrate_from_config: SiteConfiguration | None = None,
    ):
        """Enable HTTPS for a target domain and apply the resulting nginx state.

        Parameters:
            domain: Destination hostname being enabled for HTTPS.
            use_local: Whether to issue a local/self-signed certificate flow.
            use_godaddy: Whether the certbot flow should use GoDaddy DNS challenge.
            sandbox_override: Optional explicit override for DNS sandbox behavior.
            sudo: Prefix used for shell commands requiring privileged access.
            reload: Whether nginx should be reloaded after config changes.
            force_renewal: Whether certificate issuance should force renewal.
            godaddy_credential_key: Optional GoDaddy credential selector used for DNS-01 flows.
            static_ip: Optional public IP published to GoDaddy for the domain.
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
            godaddy_credential_key=godaddy_credential_key,
        )

        _warn_if_certificate_expiring_soon(self, certificate, warn_days=warn_days)
        SiteConfiguration.objects.filter(pk=config.pk).update(
            protocol="https", enabled=True
        )
        config.refresh_from_db(fields=["protocol", "enabled"])
        self._ensure_managed_site(domain, require_https=True)
        _apply_config(self, config, reload=reload)
        if use_godaddy and static_ip:
            certbot_certificate = getattr(certificate, "_specific_certificate", None)
            selected_credential = getattr(certbot_certificate, "dns_credential", None)
            transaction.on_commit(
                lambda: self._upsert_godaddy_site_record(
                    domain=domain,
                    static_ip=static_ip,
                    key=godaddy_credential_key,
                    credential=selected_credential,
                    sandbox_override=sandbox_override,
                )
            )
        return certificate

    def _parse_public_ip(self, value: str) -> str | None:
        """Validate optional ``--static-ip`` value as a public-routable address."""

        if not value:
            return None
        try:
            parsed = ipaddress.ip_address(value)
        except ValueError as exc:
            raise CommandError(f"--static-ip must be a valid IPv4 or IPv6 address: {value}") from exc
        if not parsed.is_global or parsed.is_multicast:
            raise CommandError(f"--static-ip must be public-routable: {value}")
        return value

    def _upsert_godaddy_site_record(
        self,
        *,
        domain: str,
        static_ip: str,
        key: str | None,
        credential: DNSProviderCredential | None = None,
        sandbox_override: bool | None = None,
    ) -> None:
        """Publish an A/AAAA record for *domain* through the selected GoDaddy credential."""

        if credential is None:
            credential = _resolve_godaddy_credential(key=key)
        if credential is None:
            if key:
                raise CommandError(
                    f"GoDaddy credential '{key}' was not found or is disabled. "
                    "Configure it with './command.sh godaddy setup ...' and retry."
                )
            raise CommandError(
                "No enabled GoDaddy credential was found. Configure one with './command.sh godaddy setup ...'."
            )
        if credential.provider != DNSProviderCredential.Provider.GODADDY:
            raise CommandError("Selected DNS credential is not a GoDaddy credential.")

        zone, host = self._zone_and_name(domain=domain, credential=credential)
        record_type = "AAAA" if ipaddress.ip_address(static_ip).version == 6 else "A"
        payload = [{"data": static_ip, "ttl": 600}]
        if sandbox_override is True:
            base_url = "https://api.ote-godaddy.com"
        elif sandbox_override is False:
            base_url = "https://api.godaddy.com"
        else:
            base_url = credential.get_base_url()
        headers = {
            "Authorization": credential.get_auth_header(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        customer_id = credential.get_customer_id()
        if customer_id:
            headers["X-Shopper-Id"] = customer_id
        url = f"{base_url}/v1/domains/{zone}/records/{record_type}/{host or '@'}"

        try:
            response = requests.put(url, json=payload, headers=headers, timeout=30)
        except requests.RequestException as exc:
            raise CommandError(f"Failed to publish GoDaddy DNS record for {domain}: {exc}") from exc
        if response.status_code >= 400:
            raise CommandError(
                "GoDaddy DNS record publish failed for "
                f"{domain}: {response.status_code} {response.text}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Published GoDaddy {record_type} record for {domain} -> {static_ip}."
            )
        )

    def _zone_and_name(
        self,
        *,
        domain: str,
        credential: DNSProviderCredential,
    ) -> tuple[str, str]:
        """Resolve GoDaddy zone + host tuple for a fully qualified domain."""

        hostname = domain.rstrip(".").lower()
        default_domain = credential.get_default_domain().rstrip(".").lower()
        if default_domain:
            if hostname == default_domain:
                return default_domain, ""
            suffix = f".{default_domain}"
            if hostname.endswith(suffix):
                return default_domain, hostname[: -len(suffix)]
            raise CommandError(
                f"Domain '{hostname}' does not match credential default domain '{default_domain}'."
            )

        raise CommandError(
            "GoDaddy DNS credential default domain is required to publish a static DNS record safely."
        )

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
        if source_config and source_config.enabled and source_config.protocol == "https":
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
