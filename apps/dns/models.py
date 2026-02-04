from __future__ import annotations

from typing import Iterable

from django.db import models
from django.utils import timezone

from apps.base.models import Entity
from apps.sigils.fields import SigilLongAutoField, SigilShortAutoField
from apps.users.models import Profile


class DNSProviderCredential(Profile):
    """Credentials for interacting with external DNS providers."""

    owner_required = True
    class Provider(models.TextChoices):
        GODADDY = "godaddy", "GoDaddy"

    profile_fields = (
        "provider",
        "api_key",
        "api_secret",
        "customer_id",
        "default_domain",
    )

    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        default=Provider.GODADDY,
    )
    api_key = SigilShortAutoField(
        max_length=255,
        verbose_name="API key",
        help_text="API key issued by the DNS provider.",
    )
    api_secret = SigilShortAutoField(
        max_length=255,
        verbose_name="API secret",
        help_text="API secret issued by the DNS provider.",
    )
    customer_id = SigilShortAutoField(
        max_length=100,
        blank=True,
        verbose_name="Customer ID",
        help_text="Optional GoDaddy customer identifier for the account.",
    )
    default_domain = SigilShortAutoField(
        max_length=253,
        blank=True,
        help_text="Fallback domain when records omit one.",
    )
    use_sandbox = models.BooleanField(
        default=False,
        help_text="Use the GoDaddy OTE (test) environment.",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Disable to prevent deployments with this manager.",
    )

    class Meta:
        verbose_name = "DNS Credential"
        verbose_name_plural = "DNS Credentials"

    def __str__(self) -> str:  # pragma: no cover - representation only
        owner = self.owner_display()
        provider = self.get_provider_display()
        if owner:
            return f"{provider} ({owner})"
        return provider

    def get_base_url(self) -> str:
        if self.provider != self.Provider.GODADDY:
            raise ValueError("Unsupported DNS provider")
        if self.use_sandbox:
            return "https://api.ote-godaddy.com"
        return "https://api.godaddy.com"

    def get_auth_header(self) -> str:
        key = (self.resolve_sigils("api_key") or "").strip()
        secret = (self.resolve_sigils("api_secret") or "").strip()
        if not key or not secret:
            raise ValueError("API credentials are required for DNS deployment")
        return f"sso-key {key}:{secret}"

    def get_customer_id(self) -> str:
        return (self.resolve_sigils("customer_id") or "").strip()

    def get_default_domain(self) -> str:
        return (self.resolve_sigils("default_domain") or "").strip()

    def publish_dns_records(self, records: Iterable["GoDaddyDNSRecord"]):
        from apps.dns import godaddy as dns_utils

        return dns_utils.deploy_records(self, records)


class DNSProxyConfig(Entity):
    """Configuration for the local DNS proxy."""

    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Label for this proxy configuration.",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Disable to prevent the proxy from starting.",
    )
    listen_host = models.CharField(
        max_length=255,
        default="127.0.0.1",
        help_text="Local interface to bind (use 0.0.0.0 for all IPv4 interfaces).",
    )
    listen_port = models.PositiveIntegerField(
        default=5353,
        help_text="Port to bind for DNS proxying (53 requires elevated privileges).",
    )
    upstream_servers = models.JSONField(
        default=list,
        blank=True,
        help_text="Static upstream DNS servers (IP addresses).",
    )
    include_nmcli_dns = models.BooleanField(
        default=True,
        help_text="Include DNS servers discovered from the linked NMCLI connection.",
    )
    nmcli_connection = models.ForeignKey(
        "nmcli.NetworkConnection",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dns_proxy_configs",
        help_text="NMCLI connection used as an upstream DNS source.",
    )
    use_tcp_upstream = models.BooleanField(
        default=False,
        help_text="Send upstream DNS queries over TCP instead of UDP.",
    )
    timeout_seconds = models.PositiveIntegerField(
        default=2,
        help_text="Timeout for upstream DNS queries (seconds).",
    )

    class Meta:
        verbose_name = "DNS Proxy Configuration"
        verbose_name_plural = "DNS Proxy Configurations"

    def __str__(self) -> str:  # pragma: no cover - representation only
        return self.name

    def _normalize_servers(self, values: Iterable[object]) -> list[str]:
        from .proxy import parse_dns_servers

        normalized: list[str] = []
        for value in values:
            if isinstance(value, str):
                normalized.extend(parse_dns_servers(value))
        return normalized

    def get_nmcli_upstream_servers(self) -> list[str]:
        if not self.include_nmcli_dns or not self.nmcli_connection:
            return []
        connection = self.nmcli_connection
        from .proxy import parse_dns_servers

        return self._dedupe(
            parse_dns_servers(connection.ip4_dns)
            + parse_dns_servers(connection.ip6_dns)
        )

    def get_upstream_servers(self) -> list[str]:
        upstreams = self._normalize_servers(self.upstream_servers or [])
        upstreams.extend(self.get_nmcli_upstream_servers())
        return self._dedupe(upstreams)

    def get_listen_host_for_clients(self) -> str:
        host = (self.listen_host or "").strip()
        if host in {"", "0.0.0.0"}:
            return "127.0.0.1"
        if host == "::":
            return "::1"
        return host

    def get_nmcli_dns_entries(self) -> list[str]:
        return [self.get_listen_host_for_clients()]

    def to_runtime_config(self):
        from .proxy import DNSProxyRuntimeConfig

        return DNSProxyRuntimeConfig(
            listen_host=self.listen_host,
            listen_port=self.listen_port,
            upstream_servers=self.get_upstream_servers(),
            use_tcp=self.use_tcp_upstream,
            timeout_seconds=self.timeout_seconds,
        )

    @staticmethod
    def _dedupe(values: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            value = value.strip()
            if not value or value in seen:
                continue
            seen.add(value)
            deduped.append(value)
        return deduped


class DNSRecord(Entity):
    """Stored DNS configuration ready for deployment."""

    class Type(models.TextChoices):
        A = "A", "A"
        AAAA = "AAAA", "AAAA"
        CNAME = "CNAME", "CNAME"
        MX = "MX", "MX"
        NS = "NS", "NS"
        SRV = "SRV", "SRV"
        TXT = "TXT", "TXT"

    credentials = models.ForeignKey(
        "dns.DNSProviderCredential",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dns_records",
    )
    domain = SigilShortAutoField(
        max_length=253,
        help_text="Base domain such as example.com.",
    )
    name = SigilShortAutoField(
        max_length=253,
        help_text="Record host. Use @ for the zone apex.",
    )
    record_type = models.CharField(
        max_length=10,
        choices=Type.choices,
        default=Type.A,
        verbose_name="Type",
    )
    data = SigilLongAutoField(
        help_text="Record value such as an IP address or hostname.",
    )
    ttl = models.PositiveIntegerField(
        default=600,
        help_text="Time to live in seconds.",
    )
    priority = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Priority for MX and SRV records.",
    )
    port = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Port for SRV records.",
    )
    weight = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Weight for SRV records.",
    )
    service = SigilShortAutoField(
        max_length=50,
        blank=True,
        help_text="Service label for SRV records (for example _sip).",
    )
    protocol = SigilShortAutoField(
        max_length=10,
        blank=True,
        help_text="Protocol label for SRV records (for example _tcp).",
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return f"{self.record_type} {self.fqdn()}"

    def get_domain(self, credentials: DNSProviderCredential | None = None) -> str:
        domain = (self.resolve_sigils("domain") or "").strip()
        if domain:
            return domain.rstrip(".")
        if credentials:
            fallback = credentials.get_default_domain()
            if fallback:
                return fallback.rstrip(".")
        return ""

    def get_name(self) -> str:
        name = (self.resolve_sigils("name") or "").strip()
        return name or "@"

    def fqdn(self, credentials: DNSProviderCredential | None = None) -> str:
        domain = self.get_domain(credentials)
        name = self.get_name()
        if name in {"@", ""}:
            return domain
        if name.endswith("."):
            return name.rstrip(".")
        if domain:
            return f"{name}.{domain}".rstrip(".")
        return name.rstrip(".")

    def mark_deployed(self, credentials: DNSProviderCredential | None = None, timestamp=None) -> None:
        if timestamp is None:
            timestamp = timezone.now()
        update_fields = ["last_synced_at", "last_error"]
        self.last_synced_at = timestamp
        self.last_error = ""
        if credentials and self.credentials_id != getattr(credentials, "pk", None):
            self.credentials = credentials
            update_fields.append("credentials")
        self.save(update_fields=update_fields)

    def mark_error(self, message: str, credentials: DNSProviderCredential | None = None) -> None:
        update_fields = ["last_error"]
        self.last_error = message
        if credentials and self.credentials_id != getattr(credentials, "pk", None):
            self.credentials = credentials
            update_fields.append("credentials")
        self.save(update_fields=update_fields)


class GoDaddyDNSRecord(DNSRecord):
    class Meta:
        verbose_name = "GoDaddy Record"
        verbose_name_plural = "GoDaddy Records"
        db_table = "nodes_dnsrecord"

    def to_godaddy_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "data": (self.resolve_sigils("data") or "").strip(),
            "ttl": self.ttl,
        }
        if self.priority is not None:
            payload["priority"] = self.priority
        if self.port is not None:
            payload["port"] = self.port
        if self.weight is not None:
            payload["weight"] = self.weight
        service = (self.resolve_sigils("service") or "").strip()
        if service:
            payload["service"] = service
        protocol = (self.resolve_sigils("protocol") or "").strip()
        if protocol:
            payload["protocol"] = protocol
        return payload
