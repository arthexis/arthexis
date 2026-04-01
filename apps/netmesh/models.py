from __future__ import annotations

from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class MeshMembership(Entity):
    """Represents a node enrollment in a tenant/site scoped mesh."""

    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="netmesh_memberships",
    )
    tenant = models.CharField(
        max_length=64,
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
                fields=["node", "tenant", "site"],
                name="netmesh_unique_membership_scope",
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

    tenant = models.CharField(
        max_length=64,
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
    allowed_services = models.JSONField(
        default=list,
        blank=True,
        help_text=_("List of allowed service identifiers or protocol names."),
    )

    class Meta(Entity.Meta):
        ordering = ["tenant", "site__domain", "pk"]
        indexes = [
            models.Index(
                fields=["tenant", "site", "source_node"],
                name="netmesh_policy_scope_src_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors: dict[str, list[str]] = {}
        if not self.source_node_id and not self.source_group_id:
            errors.setdefault("source_node", []).append(
                _("Choose a source node or source group."),
            )
        if not self.destination_node_id and not self.destination_group_id:
            errors.setdefault("destination_node", []).append(
                _("Choose a destination node or destination group."),
            )
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
    nat_type = models.CharField(
        max_length=16,
        choices=NatType.choices,
        default=NatType.UNKNOWN,
    )
    discovered_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True)

    class Meta(Entity.Meta):
        ordering = ["node__hostname", "-last_seen", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "endpoint"],
                name="netmesh_node_endpoint_unique",
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
    port = models.PositiveIntegerField()
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
