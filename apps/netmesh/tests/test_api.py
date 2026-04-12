import pytest
from django.contrib.sites.models import Site
from django.test import override_settings

from apps.netmesh.models import (
    MeshMembership,
    NodeEndpoint,
    NodeKeyMaterial,
    NodeRelayConfig,
    PeerPolicy,
    RelayRegion,
    ServiceAdvertisement,
)
from apps.nodes.models import Node, NodeEnrollment, NodeRole
from apps.nodes.services.enrollment import issue_enrollment_token


@pytest.mark.django_db
def test_netmesh_api_requires_valid_enrollment_token(client):
    response = client.get("/api/netmesh/caller/")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "enrollment_token_missing"


@pytest.mark.django_db
@override_settings(NETMESH_OVERLAY_IPV4_CIDR="100.96.0.0/16")
def test_netmesh_api_returns_scoped_payloads_and_etag(client):
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

    NodeEndpoint.objects.create(
        node=peer_allowed,
        endpoint="wss://peer-allowed.example/ws",
        nat_type=NodeEndpoint.NatType.OPEN,
        candidate_endpoints=["https://peer-allowed.example/direct"],
        endpoint_priority=10,
    )
    NodeEndpoint.objects.create(node=peer_other_site, endpoint="wss://peer-site-b.example/ws", nat_type=NodeEndpoint.NatType.OPEN)
    relay_region = RelayRegion.objects.create(
        code="use1",
        name="US East",
        relay_endpoint="wss://relay-use1.example/mesh",
    )
    NodeRelayConfig.objects.create(
        node=peer_allowed,
        region=relay_region,
        relay_endpoint="wss://relay-override.example/mesh",
        priority=1000,
        config={"token_hint": "relay-token"},
    )
    ServiceAdvertisement.objects.create(node=peer_allowed, service_name="ocpp", port=443, protocol=ServiceAdvertisement.Protocol.HTTPS)
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
    assert peers_json["peers"][0]["overlay_ipv4"] == "100.96.0.2"
    assert peers_json["peers"][0]["transport_key"]["public_key"] == "x25519:peer-public-key"
    assert peers_json["peers"][0]["transport_key"]["type"] == NodeKeyMaterial.KeyType.X25519

    endpoints = client.get("/api/netmesh/peer-endpoints/", **headers)
    endpoints_json = endpoints.json()
    assert endpoints.status_code == 200
    assert len(endpoints_json["endpoints"]) == 1
    endpoint_payload = endpoints_json["endpoints"][0]
    assert endpoint_payload["endpoint"] == "wss://peer-allowed.example/ws"
    assert endpoint_payload["overlay_ipv4"] == "100.96.0.2"
    assert endpoint_payload["candidate_endpoints"] == ["https://peer-allowed.example/direct"]
    assert endpoint_payload["endpoint_priority"] == 10
    assert endpoint_payload["connection_candidates"][0]["path"] == "direct"
    assert endpoint_payload["connection_candidates"][-1]["path"] == "relay"
    assert endpoint_payload["connection_candidates"][-1]["region"] == "use1"
    assert "config" not in endpoint_payload["connection_candidates"][-1]
    assert endpoint_payload["transport_key"]["public_key"] == "x25519:peer-public-key"
    assert endpoint_payload["services"] == [
        {"service": "ocpp", "port": 443, "protocol": "https"}
    ]

    acl = client.get("/api/netmesh/acl/", **headers)
    acl_json = acl.json()
    assert acl.status_code == 200
    assert len(acl_json["acl"]) == 1
    assert acl_json["acl"][0]["allowed_services"] == ["heartbeat", "ocpp"]

    key_info = client.get("/api/netmesh/key-info/", **headers)
    assert key_info.status_code == 200
    assert key_info.json()["key"]["state"] == "active"
    assert key_info.json()["key"]["type"] == NodeKeyMaterial.KeyType.X25519
    assert key_info.json()["key"]["version"] == 2
    assert len(key_info.json()["key"]["fingerprint"]) == 16

    etag_response = client.get("/api/netmesh/peers/", HTTP_IF_NONE_MATCH=peers["ETag"], **headers)
    assert etag_response.status_code == 304


@pytest.mark.django_db
def test_netmesh_api_versions_change_when_overlay_lease_changes(client):
    gateway_role = NodeRole.objects.create(name="Gateway")
    service_role = NodeRole.objects.create(name="Service")
    caller = Node.objects.create(hostname="version-caller", role=gateway_role)
    peer = Node.objects.create(hostname="version-peer", role=service_role)

    enrollment, token = issue_enrollment_token(node=caller)
    enrollment.status = NodeEnrollment.Status.ACTIVE
    enrollment.save(update_fields=["status", "updated_at"])
    caller.mesh_enrollment_state = Node.MeshEnrollmentState.ENROLLED
    caller.save(update_fields=["mesh_enrollment_state"])

    MeshMembership.objects.create(node=caller, tenant="tenant-version", is_enabled=True)
    peer_membership = MeshMembership.objects.create(node=peer, tenant="tenant-version", is_enabled=True)
    PeerPolicy.objects.create(
        tenant="tenant-version",
        source_node=caller,
        destination_node=peer,
        allowed_services=["telemetry"],
    )
    NodeEndpoint.objects.create(node=peer, endpoint="wss://version-peer.example/ws", nat_type=NodeEndpoint.NatType.OPEN)

    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
    peers_before = client.get("/api/netmesh/peers/", **headers).json()["version"]
    endpoints_before = client.get("/api/netmesh/peer-endpoints/", **headers).json()["version"]

    peer_membership.is_enabled = False
    peer_membership.save(update_fields=["is_enabled"])
    peer_membership.is_enabled = True
    peer_membership.save(update_fields=["is_enabled"])

    peers_after = client.get("/api/netmesh/peers/", **headers).json()["version"]
    endpoints_after = client.get("/api/netmesh/peer-endpoints/", **headers).json()["version"]

    assert peers_after > peers_before
    assert endpoints_after > endpoints_before


@pytest.mark.django_db
def test_charger_profile_gets_minimal_peer_endpoint_fields(client):
    charger_role = NodeRole.objects.create(name="Charger")
    service_role = NodeRole.objects.create(name="Service")
    caller = Node.objects.create(hostname="charger-node", role=charger_role)
    peer = Node.objects.create(hostname="service-peer", role=service_role)

    enrollment, token = issue_enrollment_token(node=caller)
    enrollment.status = NodeEnrollment.Status.ACTIVE
    enrollment.save(update_fields=["status", "updated_at"])
    caller.mesh_enrollment_state = Node.MeshEnrollmentState.ENROLLED
    caller.save(update_fields=["mesh_enrollment_state"])

    MeshMembership.objects.create(node=caller, tenant="tenant-z", is_enabled=True)
    MeshMembership.objects.create(node=peer, tenant="tenant-z", is_enabled=True)
    PeerPolicy.objects.create(
        tenant="tenant-z",
        source_node=caller,
        destination_node=peer,
        allowed_services=["telemetry"],
    )
    NodeEndpoint.objects.create(node=peer, endpoint="udp://10.0.0.5:3040", nat_type=NodeEndpoint.NatType.SYMMETRIC)

    response = client.get("/api/netmesh/peer-endpoints/", HTTP_AUTHORIZATION=f"Bearer {token}")

    assert response.status_code == 200
    payload = response.json()["endpoints"][0]
    assert payload["endpoint"] == "udp://10.0.0.5:3040"
    assert payload["connection_candidates"][0]["path"] == "direct"
    assert "nat_type" not in payload
    assert "services" not in payload


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
def test_peer_endpoints_are_filtered_by_peer_policy(client):
    gateway_role = NodeRole.objects.create(name="Gateway")
    service_role = NodeRole.objects.create(name="Service")
    caller = Node.objects.create(hostname="caller-node", role=gateway_role)
    allowed_peer = Node.objects.create(hostname="allowed-peer", role=service_role)
    blocked_peer = Node.objects.create(hostname="blocked-peer", role=service_role)

    enrollment, token = issue_enrollment_token(node=caller)
    enrollment.status = NodeEnrollment.Status.ACTIVE
    enrollment.save(update_fields=["status", "updated_at"])
    caller.mesh_enrollment_state = Node.MeshEnrollmentState.ENROLLED
    caller.save(update_fields=["mesh_enrollment_state"])

    MeshMembership.objects.create(node=caller, tenant="tenant-filter", is_enabled=True)
    MeshMembership.objects.create(node=allowed_peer, tenant="tenant-filter", is_enabled=True)
    MeshMembership.objects.create(node=blocked_peer, tenant="tenant-filter", is_enabled=True)
    PeerPolicy.objects.create(
        tenant="tenant-filter",
        source_node=caller,
        destination_node=allowed_peer,
        allowed_services=["telemetry"],
    )

    NodeEndpoint.objects.create(node=allowed_peer, endpoint="wss://allowed/ws", nat_type=NodeEndpoint.NatType.OPEN)
    NodeEndpoint.objects.create(node=blocked_peer, endpoint="wss://blocked/ws", nat_type=NodeEndpoint.NatType.OPEN)

    response = client.get("/api/netmesh/peer-endpoints/", HTTP_AUTHORIZATION=f"Bearer {token}")

    assert response.status_code == 200
    assert [item["endpoint"] for item in response.json()["endpoints"]] == ["wss://allowed/ws"]


@pytest.mark.django_db
def test_peer_endpoints_keep_direct_candidates_before_relays(client):
    gateway_role = NodeRole.objects.create(name="Gateway")
    service_role = NodeRole.objects.create(name="Service")
    caller = Node.objects.create(hostname="caller-node", role=gateway_role)
    peer = Node.objects.create(hostname="service-peer", role=service_role)

    enrollment, token = issue_enrollment_token(node=caller)
    enrollment.status = NodeEnrollment.Status.ACTIVE
    enrollment.save(update_fields=["status", "updated_at"])
    caller.mesh_enrollment_state = Node.MeshEnrollmentState.ENROLLED
    caller.save(update_fields=["mesh_enrollment_state"])

    MeshMembership.objects.create(node=caller, tenant="tenant-order", is_enabled=True)
    MeshMembership.objects.create(node=peer, tenant="tenant-order", is_enabled=True)
    PeerPolicy.objects.create(
        tenant="tenant-order",
        source_node=caller,
        destination_node=peer,
        allowed_services=["ocpp"],
    )
    NodeEndpoint.objects.create(
        node=peer,
        endpoint="wss://peer/ws",
        nat_type=NodeEndpoint.NatType.OPEN,
        candidate_endpoints=["wss://peer/direct-2"],
        endpoint_priority=100,
    )
    relay_region = RelayRegion.objects.create(
        code="use1",
        name="US East",
        relay_endpoint="wss://relay-use1/mesh",
    )
    NodeRelayConfig.objects.create(
        node=peer,
        region=relay_region,
        relay_endpoint="wss://relay-override/mesh",
        priority=1,
    )

    response = client.get("/api/netmesh/peer-endpoints/", HTTP_AUTHORIZATION=f"Bearer {token}")

    assert response.status_code == 200
    candidates = response.json()["endpoints"][0]["connection_candidates"]
    assert [candidate["path"] for candidate in candidates] == ["direct", "direct", "relay"]


@pytest.mark.django_db
def test_peer_endpoints_ignore_non_list_candidate_endpoints(client):
    gateway_role = NodeRole.objects.create(name="Gateway")
    service_role = NodeRole.objects.create(name="Service")
    caller = Node.objects.create(hostname="caller-node", role=gateway_role)
    peer = Node.objects.create(hostname="service-peer", role=service_role)

    enrollment, token = issue_enrollment_token(node=caller)
    enrollment.status = NodeEnrollment.Status.ACTIVE
    enrollment.save(update_fields=["status", "updated_at"])
    caller.mesh_enrollment_state = Node.MeshEnrollmentState.ENROLLED
    caller.save(update_fields=["mesh_enrollment_state"])

    MeshMembership.objects.create(node=caller, tenant="tenant-candidates", is_enabled=True)
    MeshMembership.objects.create(node=peer, tenant="tenant-candidates", is_enabled=True)
    PeerPolicy.objects.create(
        tenant="tenant-candidates",
        source_node=caller,
        destination_node=peer,
        allowed_services=["ocpp"],
    )
    NodeEndpoint.objects.create(
        node=peer,
        endpoint="wss://peer/ws",
        candidate_endpoints={"invalid": "shape"},
        nat_type=NodeEndpoint.NatType.OPEN,
    )

    response = client.get("/api/netmesh/peer-endpoints/", HTTP_AUTHORIZATION=f"Bearer {token}")

    assert response.status_code == 200
    payload = response.json()["endpoints"][0]
    assert payload["candidate_endpoints"] == []
    assert payload["connection_candidates"] == [
        {
            "endpoint": "wss://peer/ws",
            "path": "direct",
            "priority": 100,
        }
    ]


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
