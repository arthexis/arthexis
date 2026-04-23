import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.netmesh.models import (
    MeshMembership,
    NodeKeyMaterial,
    PeerPolicy,
)
from apps.netmesh.services.key_material import ensure_active_transport_key, rotate_transport_key
from apps.nodes.models import Node, NodeRole
from apps.ocpp.models import Charger

@pytest.mark.django_db
def test_peer_policy_rejects_ambiguous_allow_and_deny_with_reordered_tag_selectors():
    source = Node.objects.create(hostname="mesh-reordered-tag-source")
    destination = Node.objects.create(hostname="mesh-reordered-tag-destination")

    PeerPolicy.objects.create(
        tenant="tenant-reordered-tags",
        source_node=source,
        source_tags=["edge", "ingress"],
        destination_node=destination,
        destination_tags=["relay", "uplink"],
        allowed_services=["telemetry"],
    )

    conflicting = PeerPolicy(
        tenant="tenant-reordered-tags",
        source_node=source,
        source_tags=["ingress", "edge"],
        destination_node=destination,
        destination_tags=["uplink", "relay"],
        denied_services=["telemetry"],
    )

    with pytest.raises(ValidationError):
        conflicting.full_clean()

@pytest.mark.django_db
def test_peer_policy_requires_non_empty_tenant():
    source = Node.objects.create(hostname="mesh-empty-policy-tenant-source")
    destination = Node.objects.create(hostname="mesh-empty-policy-tenant-destination")
    policy = PeerPolicy(
        tenant="",
        source_node=source,
        destination_node=destination,
    )

    with pytest.raises(ValidationError):
        policy.full_clean()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PeerPolicy.objects.create(
                tenant="",
                source_node=source,
                destination_node=destination,
            )

