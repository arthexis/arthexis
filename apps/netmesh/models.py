from __future__ import annotations

import ipaddress

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class MeshMembership(Entity):
    """Represents a node enrollment in a tenant/site scoped mesh."""

    DEFAULT_TENANT = "arthexis"

    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="netmesh_memberships",
    )
    tenant = models.CharField(
        max_length=64,
        default=DEFAULT_TENANT,
        blank=False,
        help_text=_("Tenant identifier for external mesh orchestration scope."),
    )
    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        related_name="netmesh_memberships",
        null=True,
        blank=True,
    )
    is_enabled = models.BooleanField(default=True)

    class Meta(Entity.Meta):
        ordering = ["tenant", "site__domain", "node__hostname", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(tenant=""),
                name="netmesh_membership_tenant_non_empty",
            ),
            models.UniqueConstraint(
                fields=["node", "tenant"],
                condition=Q(site__isnull=True),
                name="netmesh_unique_membership_default_scope",
            ),
            models.UniqueConstraint(
                fields=["node", "tenant", "site"],
                condition=Q(site__isnull=False),
                name="netmesh_unique_membership_site_scope",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display helper
        scope = self.tenant or "default"
        if self.site_id and self.site:
            scope = f"{scope}/{self.site.domain}"
        return f"{self.node} [{scope}]"

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)

        was_enabled = True
        previous_tenant = self.tenant
        previous_site_id = self.site_id
        if self.pk:
            previous_state = (
                type(self)
                .objects.filter(pk=self.pk)
                .values("is_enabled", "site_id", "tenant")
                .first()
            )
            if previous_state is None:
                previous_state = {"is_enabled": True, "site_id": self.site_id, "tenant": self.tenant}
            was_enabled = previous_state["is_enabled"]
            previous_tenant = previous_state["tenant"]
            previous_site_id = previous_state["site_id"]

        if update_fields is not None:
            if previous_tenant != self.tenant:
                update_fields.add("tenant")
            if previous_site_id != self.site_id:
                update_fields.add("site")
            kwargs["update_fields"] = sorted(update_fields)

        from apps.netmesh.services.overlay_lease import ensure_overlay_lease, release_overlay_lease

        with transaction.atomic():
            super().save(*args, **kwargs)

            if update_fields == {"is_deleted"}:
                return

            if self.is_enabled:
                ensure_overlay_lease(membership=self)
            elif was_enabled:
                release_overlay_lease(membership=self)

    def delete(self, *args, **kwargs):
        from apps.netmesh.services.overlay_lease import release_overlay_lease

        with transaction.atomic():
            release_overlay_lease(membership=self)
            super().delete(*args, **kwargs)


class MeshOverlayLease(Entity):
    """Tracks assigned overlay IPv4 addresses for mesh memberships."""

    membership = models.OneToOneField(
        MeshMembership,
        on_delete=models.CASCADE,
        related_name="overlay_lease",
    )
    tenant = models.CharField(max_length=64)
    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        related_name="netmesh_overlay_leases",
        null=True,
        blank=True,
    )
    overlay_ipv4 = models.GenericIPAddressField(protocol="IPv4", unpack_ipv4=False)

    class Meta(Entity.Meta):
        ordering = ["tenant", "site__domain", "overlay_ipv4", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(tenant=""),
                name="netmesh_overlaylease_tenant_non_empty",
            ),
            models.UniqueConstraint(
                fields=["tenant", "overlay_ipv4"],
                condition=Q(site__isnull=True),
                name="netmesh_overlaylease_unique_default_scope",
            ),
            models.UniqueConstraint(
                fields=["tenant", "site", "overlay_ipv4"],
                condition=Q(site__isnull=False),
                name="netmesh_overlaylease_unique_site_scope",
            ),
        ]

    def clean(self):
        super().clean()
        errors: dict[str, list[str]] = {}
        cidr = getattr(settings, "NETMESH_OVERLAY_IPV4_CIDR", "100.96.0.0/16")
        try:
            pool = ipaddress.IPv4Network(cidr, strict=False)
        except ValueError as exc:
            errors.setdefault("overlay_ipv4", []).append(
                _("NETMESH_OVERLAY_IPV4_CIDR configuration is invalid: %(error)s")
                % {"error": str(exc)}
            )
        else:
            ip_value = ipaddress.IPv4Address(self.overlay_ipv4)
            if ip_value not in pool:
                errors.setdefault("overlay_ipv4", []).append(
                    _("Overlay address must belong to configured pool %(pool)s.") % {"pool": str(pool)}
                )
            elif ip_value in {pool.network_address, pool.broadcast_address}:
                errors.setdefault("overlay_ipv4", []).append(
                    _("Overlay address cannot use the network or broadcast address of %(pool)s.")
                    % {"pool": str(pool)}
                )

        if errors:
            raise ValidationError(errors)


class NodeKeyMaterial(Entity):
    """Stores node public key material and lifecycle state for rotation workflows."""

    class KeyState(models.TextChoices):
        ACTIVE = "active", _("Active")
        RETIRED = "retired", _("Retired")
        RETIRING = "retiring", _("Retiring")

    class KeyType(models.TextChoices):
        RSA_BOOTSTRAP = "rsa-bootstrap", _("RSA bootstrap")
        X25519 = "x25519", _("X25519")

    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="netmesh_keys",
    )
    key_type = models.CharField(
        max_length=32,
        choices=KeyType.choices,
        default=KeyType.X25519,
    )
    key_version = models.PositiveIntegerField(default=1)
    public_key = models.TextField()
    key_state = models.CharField(
        max_length=16,
        choices=KeyState.choices,
        default=KeyState.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)

    class Meta(Entity.Meta):
        ordering = ["node__hostname", "-created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["node"],
                condition=Q(key_state="active"),
                name="netmesh_node_single_active_transport_key",
            ),
        ]

    def save(self, *args, **kwargs):
        self.revoked = self.key_state != self.KeyState.ACTIVE
        super().save(*args, **kwargs)


class PeerPolicy(Entity):
    """Defines service communication policy from a source node/group to destination."""

    DEFAULT_TENANT = MeshMembership.DEFAULT_TENANT

    tenant = models.CharField(
        max_length=64,
        default=DEFAULT_TENANT,
        blank=False,
        help_text=_("Tenant identifier that owns this policy."),
    )
    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        related_name="netmesh_peer_policies",
        null=True,
        blank=True,
    )
    source_node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="netmesh_source_policies",
        null=True,
        blank=True,
    )
    source_group = models.ForeignKey(
        "nodes.NodeRole",
        on_delete=models.CASCADE,
        related_name="netmesh_source_policies",
        null=True,
        blank=True,
    )
    source_station = models.ForeignKey(
        "ocpp.Charger",
        on_delete=models.CASCADE,
        related_name="netmesh_source_policies",
        null=True,
        blank=True,
    )
    source_tags = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Node tags (mesh capability flags) that must be present on the source."),
    )
    destination_node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="netmesh_destination_policies",
        null=True,
        blank=True,
    )
    destination_group = models.ForeignKey(
        "nodes.NodeRole",
        on_delete=models.CASCADE,
        related_name="netmesh_destination_policies",
        null=True,
        blank=True,
    )
    destination_station = models.ForeignKey(
        "ocpp.Charger",
        on_delete=models.CASCADE,
        related_name="netmesh_destination_policies",
        null=True,
        blank=True,
    )
    destination_tags = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Node tags (mesh capability flags) that must be present on the destination."),
    )
    allowed_services = models.JSONField(
        default=list,
        blank=True,
        help_text=_("List of allowed service identifiers or protocol names."),
    )
    denied_services = models.JSONField(
        default=list,
        blank=True,
        help_text=_("List of denied service identifiers or protocol names."),
    )

    class Meta(Entity.Meta):
        ordering = ["tenant", "site__domain", "pk"]
        indexes = [
            models.Index(
                fields=["tenant", "site", "source_node"],
                name="netmesh_policy_scope_src_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(tenant=""),
                name="netmesh_peerpolicy_tenant_non_empty",
            ),
        ]

    @staticmethod
    def _normalize_services(services) -> list[str]:
        if not isinstance(services, list):
            return []
        normalized: list[str] = []
        for item in services:
            if not isinstance(item, str):
                continue
            value = item.strip().lower()
            if value and value not in normalized:
                normalized.append(value)
        return normalized

    def normalized_allowed_services(self) -> list[str]:
        return self._normalize_services(self.allowed_services)

    def normalized_denied_services(self) -> list[str]:
        return self._normalize_services(self.denied_services)

    def normalized_source_tags(self) -> list[str]:
        return sorted(self._normalize_services(self.source_tags))

    def normalized_destination_tags(self) -> list[str]:
        return sorted(self._normalize_services(self.destination_tags))

    def clean(self):
        super().clean()
        errors: dict[str, list[str]] = {}
        source_selectors = [bool(self.source_node_id), bool(self.source_group_id), bool(self.source_station_id)]
        source_tags = self.normalized_source_tags()
        if not any(source_selectors) and not source_tags:
            errors.setdefault("source_node", []).append(
                _("Choose a source node, source group, source station, or source tag selector."),
            )
        elif sum(source_selectors) > 1:
            errors.setdefault("source_node", []).append(
                _("Provide at most one source entity selector: node, group, or station."),
            )

        destination_selectors = [
            bool(self.destination_node_id),
            bool(self.destination_group_id),
            bool(self.destination_station_id),
        ]
        destination_tags = self.normalized_destination_tags()
        if not any(destination_selectors) and not destination_tags:
            errors.setdefault("destination_node", []).append(
                _("Choose a destination node, destination group, destination station, or destination tag selector."),
            )
        elif sum(destination_selectors) > 1:
            errors.setdefault("destination_node", []).append(
                _("Provide at most one destination entity selector: node, group, or station."),
            )

        allowed_services = self.normalized_allowed_services()
        denied_services = self.normalized_denied_services()
        overlap = sorted(set(allowed_services).intersection(denied_services))
        if overlap:
            errors.setdefault("denied_services", []).append(
                _("Services cannot be allowed and denied in the same policy: %(services)s.")
                % {"services": ", ".join(overlap)}
            )

        conflicting = (
            PeerPolicy.objects.filter(
                tenant=self.tenant,
                site_id=self.site_id,
                source_node_id=self.source_node_id,
                source_group_id=self.source_group_id,
                source_station_id=self.source_station_id,
                source_tags=source_tags,
                destination_node_id=self.destination_node_id,
                destination_group_id=self.destination_group_id,
                destination_station_id=self.destination_station_id,
                destination_tags=destination_tags,
            )
            .exclude(pk=self.pk)
            .only("id", "allowed_services", "denied_services")
        )
        for policy in conflicting:
            policy_allowed = set(policy.normalized_allowed_services())
            policy_denied = set(policy.normalized_denied_services())
            ambiguity = sorted((set(allowed_services) & policy_denied) | (set(denied_services) & policy_allowed))
            if ambiguity:
                errors.setdefault("allowed_services", []).append(
                    _("Conflicting allow/deny services already exist for this selector set: %(services)s.")
                    % {"services": ", ".join(ambiguity)}
                )
                break

        self.source_tags = source_tags
        self.destination_tags = destination_tags
        self.allowed_services = allowed_services
        self.denied_services = denied_services

        if errors:
            raise ValidationError(errors)


class NodeEndpoint(Entity):
    """Tracks discovered endpoints for mesh-capable nodes."""

    class NatType(models.TextChoices):
        UNKNOWN = "UNKNOWN", _("Unknown")
        OPEN = "OPEN", _("Open")
        RESTRICTED = "RESTRICTED", _("Restricted")
        SYMMETRIC = "SYMMETRIC", _("Symmetric")

    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="netmesh_endpoints",
    )
    endpoint = models.CharField(max_length=255)
    candidate_endpoints = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Additional direct endpoints agents should try for this node."),
    )
    endpoint_priority = models.PositiveSmallIntegerField(default=100)
    nat_type = models.CharField(
        max_length=16,
        choices=NatType.choices,
        default=NatType.UNKNOWN,
    )
    discovered_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    last_successful_direct_at = models.DateTimeField(null=True, blank=True)
    relay_required = models.BooleanField(default=False)
    relay_reason = models.CharField(max_length=255, blank=True)

    class Meta(Entity.Meta):
        ordering = ["node__hostname", "-last_seen", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "endpoint"],
                name="netmesh_node_endpoint_unique",
            ),
        ]


class RelayRegion(Entity):
    """Defines relay region metadata for DERP-like relay coordination."""

    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=100)
    relay_endpoint = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta(Entity.Meta):
        ordering = ["code", "pk"]

    def __str__(self) -> str:  # pragma: no cover - admin display helper
        return f"{self.code} ({self.name})"


class NodeRelayConfig(Entity):
    """Stores node-specific relay preferences and fallback endpoint configuration."""

    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="netmesh_relay_configs",
    )
    region = models.ForeignKey(
        RelayRegion,
        on_delete=models.CASCADE,
        related_name="node_configs",
    )
    relay_endpoint = models.CharField(max_length=255, blank=True)
    config = models.JSONField(default=dict, blank=True)
    priority = models.PositiveSmallIntegerField(default=1000)
    is_enabled = models.BooleanField(default=True)

    class Meta(Entity.Meta):
        ordering = ["node__hostname", "priority", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "region"],
                name="netmesh_node_relay_region_unique",
            ),
        ]


class ServiceAdvertisement(Entity):
    """Service advertisement emitted by a node for peer routing decisions."""

    class Protocol(models.TextChoices):
        TCP = "tcp", _("TCP")
        UDP = "udp", _("UDP")
        HTTP = "http", _("HTTP")
        HTTPS = "https", _("HTTPS")

    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="netmesh_service_ads",
    )
    service_name = models.CharField(max_length=100)
    port = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
    )
    protocol = models.CharField(max_length=8, choices=Protocol.choices, default=Protocol.TCP)
    route_metadata = models.JSONField(default=dict, blank=True)

    class Meta(Entity.Meta):
        ordering = ["node__hostname", "service_name", "port", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "service_name", "port", "protocol"],
                name="netmesh_service_ad_unique",
            ),
        ]


class NetmeshAgentStatus(Entity):
    """Operational status row for the resident Netmesh agent loop."""

    singleton = models.CharField(max_length=32, unique=True, default="default")
    is_running = models.BooleanField(default=False)
    lifecycle_state = models.CharField(max_length=64, default="idle")
    last_poll_at = models.DateTimeField(null=True, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    peers_synced = models.PositiveIntegerField(default=0)
    session_count = models.PositiveIntegerField(default=0)
    relay_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta(Entity.Meta):
        ordering = ["singleton", "pk"]

    @classmethod
    def get_solo(cls):
        status, _ = cls.objects.get_or_create(singleton="default")
        return status
