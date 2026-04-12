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
from apps.netmesh.services.key_material import ensure_active_transport_key, rotate_transport_key
from apps.nodes.models import Node, NodeRole
from apps.ocpp.models import Charger


@pytest.mark.django_db
def test_node_key_material_only_one_active_key():
    node = Node.objects.create(hostname="mesh-a")

    NodeKeyMaterial.objects.create(node=node, public_key="pk-1", revoked=False)

    with pytest.raises(IntegrityError):
        NodeKeyMaterial.objects.create(node=node, public_key="pk-2", revoked=False)


@pytest.mark.django_db
def test_rotate_transport_key_creates_x25519_transport_identity_separate_from_bootstrap_key():
    node = Node.objects.create(hostname="mesh-key-rotate", public_key="ssh-rsa bootstrap")
    previous = NodeKeyMaterial.objects.create(
        node=node,
        key_type=NodeKeyMaterial.KeyType.RSA_BOOTSTRAP,
        key_state=NodeKeyMaterial.KeyState.ACTIVE,
        public_key="ssh-rsa bootstrap",
        key_version=1,
        revoked=False,
    )

    rotated, private_key = rotate_transport_key(node=node)
    previous.refresh_from_db()

    assert rotated.key_type == NodeKeyMaterial.KeyType.X25519
    assert rotated.key_state == NodeKeyMaterial.KeyState.ACTIVE
    assert rotated.public_key.startswith("x25519:")
    assert rotated.public_key != node.public_key
    assert rotated.key_version == 2
    assert previous.key_state == NodeKeyMaterial.KeyState.RETIRED
    assert previous.revoked is True
    assert previous.rotated_at is not None
    assert private_key


@pytest.mark.django_db
def test_ensure_active_transport_key_replaces_active_bootstrap_key():
    node = Node.objects.create(hostname="mesh-key-ensure", public_key="ssh-rsa bootstrap")
    previous = NodeKeyMaterial.objects.create(
        node=node,
        key_type=NodeKeyMaterial.KeyType.RSA_BOOTSTRAP,
        key_state=NodeKeyMaterial.KeyState.ACTIVE,
        public_key="ssh-rsa bootstrap",
        key_version=1,
        revoked=False,
    )

    ensured, private_key = ensure_active_transport_key(node=node)
    previous.refresh_from_db()

    assert ensured.key_type == NodeKeyMaterial.KeyType.X25519
    assert ensured.key_state == NodeKeyMaterial.KeyState.ACTIVE
    assert ensured.public_key.startswith("x25519:")
    assert ensured.key_version == 2
    assert previous.key_state == NodeKeyMaterial.KeyState.RETIRED
    assert previous.revoked is True
    assert previous.rotated_at is not None
    assert private_key


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
    policy = PeerPolicy(source_node=source_node)

    with pytest.raises(ValidationError):
        policy.full_clean()


@pytest.mark.django_db
def test_peer_policy_accepts_station_and_tag_selectors():
    source = Node.objects.create(hostname="mesh-source-station", mesh_capability_flags=["edge"])
    destination = Node.objects.create(hostname="mesh-destination-station", mesh_capability_flags=["ingress"])
    station = Charger.objects.create(charger_id="NETMESH-STATION-1", manager_node=source)
    policy = PeerPolicy(
        source_station=station,
        source_tags=["edge"],
        destination_node=destination,
        destination_tags=["ingress"],
        allowed_services=["telemetry"],
    )

    policy.full_clean()


@pytest.mark.django_db
def test_peer_policy_rejects_ambiguous_allow_and_deny_combinations():
    source = Node.objects.create(hostname="mesh-allow-deny-source")
    destination = Node.objects.create(hostname="mesh-allow-deny-destination")
    policy = PeerPolicy(
        tenant="tenant-ambiguous",
        source_node=source,
        destination_node=destination,
        allowed_services=["telemetry"],
        denied_services=["telemetry"],
    )

    with pytest.raises(ValidationError):
        policy.full_clean()

    PeerPolicy.objects.create(
        tenant="tenant-ambiguous",
        source_node=source,
        destination_node=destination,
        allowed_services=["telemetry"],
    )
    conflicting = PeerPolicy(
        tenant="tenant-ambiguous",
        source_node=source,
        destination_node=destination,
        denied_services=["telemetry"],
    )

    with pytest.raises(ValidationError):
        conflicting.full_clean()


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
def test_mesh_membership_disallows_duplicate_default_scope():
    node = Node.objects.create(hostname="mesh-default-scope")

    MeshMembership.objects.create(node=node, tenant=MeshMembership.DEFAULT_TENANT, site=None)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            MeshMembership.objects.create(node=node, tenant=MeshMembership.DEFAULT_TENANT, site=None)


@pytest.mark.django_db
def test_mesh_membership_requires_non_empty_tenant():
    node = Node.objects.create(hostname="mesh-empty-membership-tenant")
    membership = MeshMembership(node=node, tenant="")

    with pytest.raises(ValidationError):
        membership.full_clean()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            MeshMembership.objects.create(node=node, tenant="")


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
