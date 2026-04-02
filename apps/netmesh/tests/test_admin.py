import pytest
from django.contrib import admin
from django.contrib.admin import helpers
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from apps.netmesh.admin import MeshMembershipAdmin, NodeEndpointAdmin, PeerPolicyAdmin
from apps.netmesh.models import MeshMembership, NodeEndpoint, PeerPolicy
from apps.nodes.models import Node


def _attach_messages(request):
    setattr(request, "session", {})
    setattr(request, "_messages", FallbackStorage(request))


@pytest.mark.django_db
def test_mesh_membership_admin_quarantine_segment_disables_selected_memberships():
    user = get_user_model().objects.create_superuser("root", "root@example.com", "password")
    node = Node.objects.create(hostname="mesh-node-admin")
    membership = MeshMembership.objects.create(node=node, tenant="tenant-a", is_enabled=True)
    model_admin = MeshMembershipAdmin(MeshMembership, admin.site)

    request = RequestFactory().post(
        "/admin/netmesh/meshmembership/",
        data={
            "apply": "1",
            helpers.ACTION_CHECKBOX_NAME: [str(membership.pk)],
        },
    )
    request.user = user
    _attach_messages(request)

    response = model_admin.quarantine_segment(request, MeshMembership.objects.filter(pk=membership.pk))

    membership.refresh_from_db()
    assert response.status_code == 302
    assert membership.is_enabled is False


@pytest.mark.django_db
def test_mesh_membership_admin_revoke_selected_nodes_disables_membership_and_node_enrollment():
    user = get_user_model().objects.create_superuser("root2", "root2@example.com", "password")
    node = Node.objects.create(hostname="mesh-node-revoke", mesh_enrollment_state=Node.MeshEnrollmentState.ENROLLED)
    membership = MeshMembership.objects.create(node=node, tenant="tenant-a", is_enabled=True)
    model_admin = MeshMembershipAdmin(MeshMembership, admin.site)

    request = RequestFactory().post(
        "/admin/netmesh/meshmembership/",
        data={
            "apply": "1",
            helpers.ACTION_CHECKBOX_NAME: [str(membership.pk)],
        },
    )
    request.user = user
    _attach_messages(request)

    response = model_admin.revoke_selected_nodes(request, MeshMembership.objects.filter(pk=membership.pk))

    membership.refresh_from_db()
    node.refresh_from_db()
    assert response.status_code == 302
    assert membership.is_enabled is False
    assert node.mesh_enrollment_state == Node.MeshEnrollmentState.UNENROLLED


@pytest.mark.django_db
def test_peer_policy_admin_changelist_exposes_policy_matrix_url(admin_client):
    response = admin_client.get("/admin/netmesh/peerpolicy/")

    assert response.status_code == 200
    assert "policy_matrix_url" in response.context_data


@pytest.mark.django_db
def test_node_endpoint_admin_health_status_classes():
    model_admin = NodeEndpointAdmin(NodeEndpoint, admin.site)
    endpoint = NodeEndpoint(node=Node(hostname="health-node"), endpoint="198.51.100.10:443")

    rendered = model_admin.health_status(endpoint)

    assert "degraded" in str(rendered)


@pytest.mark.django_db
def test_peer_policy_matrix_view_renders(admin_client):
    node = Node.objects.create(hostname="policy-node")
    PeerPolicy.objects.create(source_node=node, destination_node=node)

    response = admin_client.get("/admin/netmesh/peerpolicy/matrix/")

    assert response.status_code == 200
    assert "rows" in response.context_data
