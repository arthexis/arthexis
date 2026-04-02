from __future__ import annotations

from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
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
        blank=True,
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


class NodeKeyMaterial(Entity):
    """Stores node public key material and lifecycle state for rotation workflows."""

    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="netmesh_keys",
    )
    public_key = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)

    class Meta(Entity.Meta):
        ordering = ["node__hostname", "-created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["node"],
                condition=Q(revoked=False),
                name="netmesh_node_single_active_key",
            ),
        ]


class PeerPolicy(Entity):
    """Defines service communication policy from a source node/group to destination."""

    DEFAULT_TENANT = MeshMembership.DEFAULT_TENANT

    tenant = models.CharField(
        max_length=64,
        default=DEFAULT_TENANT,
        blank=True,
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
        constraints = []

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
