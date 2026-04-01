import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.netmesh.models import (
    MeshMembership,
    NodeKeyMaterial,
    NodeRelayConfig,
    PeerPolicy,
    RelayRegion,
    ServiceAdvertisement,
)
from apps.nodes.models import Node, NodeRole


@pytest.mark.django_db
def test_node_key_material_only_one_active_key():
    node = Node.objects.create(hostname="mesh-a")

    NodeKeyMaterial.objects.create(node=node, public_key="pk-1", revoked=False)

    with pytest.raises(IntegrityError):
        NodeKeyMaterial.objects.create(node=node, public_key="pk-2", revoked=False)


@pytest.mark.django_db
def test_peer_policy_requires_source_and_destination():
    policy = PeerPolicy()

    with pytest.raises(ValidationError):
        policy.full_clean()

    source_group = NodeRole.objects.create(name="Netmesh Source")
    destination_group = NodeRole.objects.create(name="Netmesh Destination")
    scoped_policy = PeerPolicy(
        source_group=source_group,
        destination_group=destination_group,
    )

    scoped_policy.full_clean()


@pytest.mark.django_db
def test_peer_policy_rejects_mixed_node_and_group_selectors():
    source_node = Node.objects.create(hostname="mesh-source")
    destination_node = Node.objects.create(hostname="mesh-destination")
    source_group = NodeRole.objects.create(name="Source Group")
    destination_group = NodeRole.objects.create(name="Destination Group")
    policy = PeerPolicy(
        source_node=source_node,
        source_group=source_group,
        destination_node=destination_node,
        destination_group=destination_group,
    )

    with pytest.raises(ValidationError):
        policy.full_clean()


@pytest.mark.django_db
def test_peer_policy_selector_constraints_block_direct_create():
    source_node = Node.objects.create(hostname="mesh-source-db")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PeerPolicy.objects.create(source_node=source_node)


@pytest.mark.django_db
def test_mesh_membership_disallows_duplicate_default_scope():
    node = Node.objects.create(hostname="mesh-default-scope")

    MeshMembership.objects.create(node=node, tenant="", site=None)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            MeshMembership.objects.create(node=node, tenant="", site=None)


@pytest.mark.django_db
def test_service_advertisement_port_range_validation():
    node = Node.objects.create(hostname="mesh-port")
    out_of_range_port = ServiceAdvertisement(
        node=node,
        service_name="svc",
        protocol=ServiceAdvertisement.Protocol.TCP,
        port=65536,
    )

    with pytest.raises(ValidationError):
        out_of_range_port.full_clean()


@pytest.mark.django_db
def test_node_relay_config_unique_per_region():
    node = Node.objects.create(hostname="mesh-relay-node")
    region = RelayRegion.objects.create(
        code="usw2",
        name="US West",
        relay_endpoint="wss://relay-usw2.example/mesh",
    )
    NodeRelayConfig.objects.create(node=node, region=region)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            NodeRelayConfig.objects.create(node=node, region=region)
