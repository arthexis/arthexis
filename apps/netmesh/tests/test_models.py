import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.netmesh.models import NodeKeyMaterial, PeerPolicy
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
