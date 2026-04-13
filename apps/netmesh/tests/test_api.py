import pytest
from django.contrib.sites.models import Site

from apps.netmesh.models import MeshMembership, NodeKeyMaterial, PeerPolicy
from apps.nodes.models import Node, NodeEnrollment, NodeRole
from apps.nodes.services.enrollment import issue_enrollment_token


@pytest.mark.django_db
def test_netmesh_api_requires_valid_enrollment_token(client):
    response = client.get("/api/netmesh/caller/")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "enrollment_token_missing"


@pytest.mark.django_db
def test_netmesh_api_returns_scoped_task_payloads_and_etag(client):
    gateway_role = NodeRole.objects.create(name="Gateway")
    service_role = NodeRole.objects.create(name="Service")
    site_a = Site.objects.create(domain="tenant-a.example", name="Tenant A")
    site_b = Site.objects.create(domain="tenant-b.example", name="Tenant B")

    caller = Node.objects.create(hostname="caller-gw", role=gateway_role)
    peer_allowed = Node.objects.create(hostname="peer-allowed", role=service_role, public_endpoint="peer-allowed")
    peer_other_site = Node.objects.create(hostname="peer-site-b", role=service_role, public_endpoint="peer-site-b")

    enrollment, token = issue_enrollment_token(node=caller, site=site_a)
    enrollment.status = NodeEnrollment.Status.ACTIVE
    enrollment.save(update_fields=["status", "updated_at"])
    caller.mesh_enrollment_state = Node.MeshEnrollmentState.ENROLLED
    caller.save(update_fields=["mesh_enrollment_state"])

    MeshMembership.objects.create(node=caller, tenant="tenant-a", site=site_a, is_enabled=True)
    MeshMembership.objects.create(node=peer_allowed, tenant="tenant-a", site=site_a, is_enabled=True)
    MeshMembership.objects.create(node=peer_other_site, tenant="tenant-a", site=site_b, is_enabled=True)

    PeerPolicy.objects.create(
        tenant="tenant-a",
        site=site_a,
        source_node=caller,
        destination_node=peer_allowed,
        allowed_services=["ocpp", "heartbeat"],
    )
    PeerPolicy.objects.create(
        tenant="tenant-a",
        site=site_b,
        source_node=caller,
        destination_node=peer_other_site,
        allowed_services=["should-not-leak"],
    )

    NodeKeyMaterial.objects.create(
        node=caller,
        key_type=NodeKeyMaterial.KeyType.X25519,
        key_version=2,
        public_key="x25519:caller-public-key",
        key_state=NodeKeyMaterial.KeyState.ACTIVE,
        revoked=False,
    )
    NodeKeyMaterial.objects.create(
        node=peer_allowed,
        key_type=NodeKeyMaterial.KeyType.X25519,
        key_version=3,
        public_key="x25519:peer-public-key",
        key_state=NodeKeyMaterial.KeyState.ACTIVE,
        revoked=False,
    )

    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    metadata = client.get("/api/netmesh/caller/", **headers)
    assert metadata.status_code == 200
    assert metadata.json()["node"]["tenant"] == "tenant-a"
    assert metadata.json()["node"]["profile"] == "gateway"

    peers = client.get("/api/netmesh/peers/", **headers)
    peers_json = peers.json()
    assert peers.status_code == 200
    assert [item["hostname"] for item in peers_json["peers"]] == ["peer-allowed"]
    assert peers_json["peers"][0]["site_id"] == site_a.id
    assert "public_endpoint" not in peers_json["peers"][0]
    assert peers_json["peers"][0]["transport_key"]["public_key"] == "x25519:peer-public-key"
    assert peers_json["peers"][0]["task_policy"]["allowed_tasks"] == ["heartbeat", "ocpp"]

    acl = client.get("/api/netmesh/acl/", **headers)
    acl_json = acl.json()
    assert acl.status_code == 200
    assert len(acl_json["acl"]) == 1
    assert acl_json["acl"][0]["allowed_tasks"] == ["heartbeat", "ocpp"]

    key_info = client.get("/api/netmesh/key-info/", **headers)
    assert key_info.status_code == 200
    assert key_info.json()["key"]["state"] == "active"
    assert key_info.json()["key"]["type"] == NodeKeyMaterial.KeyType.X25519
    assert key_info.json()["key"]["version"] == 2
    assert len(key_info.json()["key"]["fingerprint"]) == 16

    etag_response = client.get("/api/netmesh/peers/", HTTP_IF_NONE_MATCH=peers["ETag"], **headers)
    assert etag_response.status_code == 304


@pytest.mark.django_db
def test_permitted_peers_includes_group_destination_matches(client):
    gateway_role = NodeRole.objects.create(name="Gateway")
    service_role = NodeRole.objects.create(name="Service")
    caller = Node.objects.create(hostname="caller-gateway", role=gateway_role)
    group_peer = Node.objects.create(hostname="group-peer", role=service_role, public_endpoint="wss://group-peer/ws")

    enrollment, token = issue_enrollment_token(node=caller)
    enrollment.status = NodeEnrollment.Status.ACTIVE
    enrollment.save(update_fields=["status", "updated_at"])
    caller.mesh_enrollment_state = Node.MeshEnrollmentState.ENROLLED
    caller.save(update_fields=["mesh_enrollment_state"])

    MeshMembership.objects.create(node=caller, tenant="tenant-group", is_enabled=True)
    MeshMembership.objects.create(node=group_peer, tenant="tenant-group", is_enabled=True)
    PeerPolicy.objects.create(
        tenant="tenant-group",
        source_node=caller,
        destination_group=service_role,
        allowed_services=["ocpp"],
    )

    response = client.get("/api/netmesh/peers/", HTTP_AUTHORIZATION=f"Bearer {token}")

    assert response.status_code == 200
    assert [item["hostname"] for item in response.json()["peers"]] == ["group-peer"]


@pytest.mark.django_db
@pytest.mark.critical
def test_netmesh_token_lifecycle_errors_are_stable(client):
    role = NodeRole.objects.create(name="Gateway")
    node = Node.objects.create(hostname="lifecycle-node", role=role)

    enrollment, token = issue_enrollment_token(node=node, scope="mesh:read")
    enrollment.status = NodeEnrollment.Status.ACTIVE
    enrollment.save(update_fields=["status", "updated_at"])
    node.mesh_enrollment_state = Node.MeshEnrollmentState.ENROLLED
    node.save(update_fields=["mesh_enrollment_state"])

    MeshMembership.objects.create(node=node, tenant="tenant-lifecycle", is_enabled=True)

    ok = client.get("/api/netmesh/caller/", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert ok.status_code == 200

    wrong_scope_enrollment, wrong_scope_token = issue_enrollment_token(node=node, scope="ocpp:control")
    wrong_scope_enrollment.status = NodeEnrollment.Status.ACTIVE
    wrong_scope_enrollment.save(update_fields=["status", "updated_at"])
    node.mesh_enrollment_state = Node.MeshEnrollmentState.ENROLLED
    node.save(update_fields=["mesh_enrollment_state"])
    wrong_scope = client.get("/api/netmesh/caller/", HTTP_AUTHORIZATION=f"Bearer {wrong_scope_token}")
    assert wrong_scope.status_code == 403
    assert wrong_scope.json()["error"]["code"] == "enrollment_scope_insufficient"

    malformed = client.get("/api/netmesh/caller/", HTTP_AUTHORIZATION="Bearer not-a-real-token")
    assert malformed.status_code == 401
    assert malformed.json()["error"]["code"] == "enrollment_token_invalid"

    enrollment.status = NodeEnrollment.Status.REVOKED
    enrollment.revoked_at = enrollment.expires_at
    enrollment.save(update_fields=["status", "revoked_at", "updated_at"])
    revoked = client.get("/api/netmesh/caller/", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "enrollment_token_revoked"

    expired_enrollment, expired_token = issue_enrollment_token(node=node, scope="mesh:read")
    expired_enrollment.status = NodeEnrollment.Status.ACTIVE
    expired_enrollment.expires_at = expired_enrollment.created_at
    expired_enrollment.save(update_fields=["status", "expires_at", "updated_at"])
    expired = client.get("/api/netmesh/caller/", HTTP_AUTHORIZATION=f"Bearer {expired_token}")
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "enrollment_token_expired"
