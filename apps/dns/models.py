from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.base.models import Entity
from apps.sigils.fields import SigilLongAutoField, SigilShortAutoField


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

    node_manager = models.ForeignKey(
        "nodes.NodeManager",
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

    def get_domain(self, manager=None) -> str:
        domain = (self.resolve_sigils("domain") or "").strip()
        if domain:
            return domain.rstrip(".")
        if manager:
            fallback = manager.get_default_domain()
            if fallback:
                return fallback.rstrip(".")
        return ""

    def get_name(self) -> str:
        name = (self.resolve_sigils("name") or "").strip()
        return name or "@"

    def fqdn(self, manager=None) -> str:
        domain = self.get_domain(manager)
        name = self.get_name()
        if name in {"@", ""}:
            return domain
        if name.endswith("."):
            return name.rstrip(".")
        if domain:
            return f"{name}.{domain}".rstrip(".")
        return name.rstrip(".")

    def mark_deployed(self, manager=None, timestamp=None) -> None:
        if timestamp is None:
            timestamp = timezone.now()
        update_fields = ["last_synced_at", "last_error"]
        self.last_synced_at = timestamp
        self.last_error = ""
        if manager and self.node_manager_id != getattr(manager, "pk", None):
            self.node_manager = manager
            update_fields.append("node_manager")
        self.save(update_fields=update_fields)

    def mark_error(self, message: str, manager=None) -> None:
        update_fields = ["last_error"]
        self.last_error = message
        if manager and self.node_manager_id != getattr(manager, "pk", None):
            self.node_manager = manager
            update_fields.append("node_manager")
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
