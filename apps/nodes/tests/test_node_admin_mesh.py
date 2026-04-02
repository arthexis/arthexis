import pytest
from django.contrib import admin

from apps.netmesh.models import NodeKeyMaterial
from apps.nodes.admin.node_admin import NodeAdmin
from apps.nodes.models import Node


@pytest.mark.django_db
def test_node_admin_mesh_status_badge_renders_label():
    node = Node.objects.create(hostname="node-mesh-badge", mesh_enrollment_state=Node.MeshEnrollmentState.ENROLLED)
    model_admin = NodeAdmin(Node, admin.site)

    rendered = model_admin.mesh_status_badge(node)

    assert "Enrolled" in str(rendered)


@pytest.mark.django_db
def test_node_admin_mesh_key_age_without_active_key():
    node = Node.objects.create(hostname="node-mesh-key-age")
    model_admin = NodeAdmin(Node, admin.site)

    rendered = model_admin.mesh_key_age(node)

    assert "No active key" in str(rendered)


@pytest.mark.django_db
def test_node_admin_mesh_key_age_with_active_key():
    node = Node.objects.create(hostname="node-mesh-key-age-active")
    NodeKeyMaterial.objects.create(node=node, public_key="ssh-rsa test")
    model_admin = NodeAdmin(Node, admin.site)

    rendered = model_admin.mesh_key_age(node)

    assert "days" in str(rendered)
