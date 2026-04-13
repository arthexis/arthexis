from __future__ import annotations

from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
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


class NetmeshAgentStatus(Entity):
    """Operational status row for the resident Netmesh agent loop."""

    singleton = models.CharField(max_length=32, unique=True, default="default")
    is_running = models.BooleanField(default=False)
    lifecycle_state = models.CharField(max_length=64, default="idle")
    last_poll_at = models.DateTimeField(null=True, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    peers_synced = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta(Entity.Meta):
        ordering = ["singleton", "pk"]

    @classmethod
    def get_solo(cls):
        status, _ = cls.objects.get_or_create(singleton="default")
        return status
