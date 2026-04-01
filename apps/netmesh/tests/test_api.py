import pytest
from django.contrib.sites.models import Site

from apps.netmesh.models import MeshMembership, NodeEndpoint, NodeKeyMaterial, PeerPolicy, ServiceAdvertisement
from apps.nodes.models import Node, NodeEnrollment, NodeRole
from apps.nodes.services.enrollment import issue_enrollment_token


@pytest.mark.django_db
def test_netmesh_api_requires_valid_enrollment_token(client):
    response = client.get("/api/netmesh/caller/")

    assert response.status_code == 401
    assert response.json()["detail"] == "missing enrollment token"


@pytest.mark.django_db
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

    NodeEndpoint.objects.create(node=peer_allowed, endpoint="wss://peer-allowed.example/ws", nat_type=NodeEndpoint.NatType.OPEN)
    NodeEndpoint.objects.create(node=peer_other_site, endpoint="wss://peer-site-b.example/ws", nat_type=NodeEndpoint.NatType.OPEN)
    ServiceAdvertisement.objects.create(node=peer_allowed, service_name="ocpp", port=443, protocol=ServiceAdvertisement.Protocol.HTTPS)
    NodeKeyMaterial.objects.create(node=caller, public_key="caller-public-key", revoked=False)

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

    endpoints = client.get("/api/netmesh/peer-endpoints/", **headers)
    endpoints_json = endpoints.json()
    assert endpoints.status_code == 200
    assert len(endpoints_json["endpoints"]) == 1
    assert endpoints_json["endpoints"][0]["endpoint"] == "wss://peer-allowed.example/ws"
    assert endpoints_json["endpoints"][0]["services"] == [
        {"service": "ocpp", "port": 443, "protocol": "https"}
    ]

    acl = client.get("/api/netmesh/acl/", **headers)
    acl_json = acl.json()
    assert acl.status_code == 200
    assert len(acl_json["acl"]) == 1
    assert acl_json["acl"][0]["allowed_services"] == ["ocpp", "heartbeat"]

    key_info = client.get("/api/netmesh/key-info/", **headers)
    assert key_info.status_code == 200
    assert key_info.json()["key"]["state"] == "active"
    assert len(key_info.json()["key"]["fingerprint"]) == 16

    etag_response = client.get("/api/netmesh/peers/", HTTP_IF_NONE_MATCH=peers["ETag"], **headers)
    assert etag_response.status_code == 304
    wildcard_etag_response = client.get("/api/netmesh/peers/", HTTP_IF_NONE_MATCH="*", **headers)
    assert wildcard_etag_response.status_code == 304


@pytest.mark.django_db
def test_group_destination_policy_is_included_in_peers_and_endpoints(client):
    gateway_role = NodeRole.objects.create(name="Gateway")
    charger_role = NodeRole.objects.create(name="Charger")
    service_role = NodeRole.objects.create(name="Service")

    caller = Node.objects.create(hostname="caller-gw", role=gateway_role)
    group_peer = Node.objects.create(hostname="group-peer", role=charger_role, public_endpoint="group-peer")
    blocked_peer = Node.objects.create(hostname="blocked-peer", role=service_role, public_endpoint="blocked-peer")

    enrollment, token = issue_enrollment_token(node=caller)
    enrollment.status = NodeEnrollment.Status.ACTIVE
    enrollment.save(update_fields=["status", "updated_at"])
    caller.mesh_enrollment_state = Node.MeshEnrollmentState.ENROLLED
    caller.save(update_fields=["mesh_enrollment_state"])

    MeshMembership.objects.create(node=caller, tenant="tenant-a", is_enabled=True)
    MeshMembership.objects.create(node=group_peer, tenant="tenant-a", is_enabled=True)
    MeshMembership.objects.create(node=blocked_peer, tenant="tenant-a", is_enabled=True)

    PeerPolicy.objects.create(
        tenant="tenant-a",
        source_node=caller,
        destination_group=charger_role,
        allowed_services=["telemetry"],
    )

    NodeEndpoint.objects.create(node=group_peer, endpoint="wss://group-peer.example/ws", nat_type=NodeEndpoint.NatType.OPEN)
    NodeEndpoint.objects.create(node=blocked_peer, endpoint="wss://blocked-peer.example/ws", nat_type=NodeEndpoint.NatType.OPEN)

    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
    peers = client.get("/api/netmesh/peers/", **headers)
    endpoints = client.get("/api/netmesh/peer-endpoints/", **headers)

    assert peers.status_code == 200
    assert [item["hostname"] for item in peers.json()["peers"]] == ["group-peer"]
    assert endpoints.status_code == 200
    assert [item["endpoint"] for item in endpoints.json()["endpoints"]] == ["wss://group-peer.example/ws"]


@pytest.mark.django_db
def test_peer_endpoints_excludes_policy_nodes_without_active_scoped_membership(client):
    gateway_role = NodeRole.objects.create(name="Gateway")
    service_role = NodeRole.objects.create(name="Service")
    site_a = Site.objects.create(domain="tenant-a.example", name="Tenant A")
    site_b = Site.objects.create(domain="tenant-b.example", name="Tenant B")

    caller = Node.objects.create(hostname="caller-gw", role=gateway_role)
    in_scope_peer = Node.objects.create(hostname="in-scope-peer", role=service_role)
    disabled_peer = Node.objects.create(hostname="disabled-peer", role=service_role)
    other_site_peer = Node.objects.create(hostname="other-site-peer", role=service_role)

    enrollment, token = issue_enrollment_token(node=caller, site=site_a)
    enrollment.status = NodeEnrollment.Status.ACTIVE
    enrollment.save(update_fields=["status", "updated_at"])
    caller.mesh_enrollment_state = Node.MeshEnrollmentState.ENROLLED
    caller.save(update_fields=["mesh_enrollment_state"])

    MeshMembership.objects.create(node=caller, tenant="tenant-a", site=site_a, is_enabled=True)
    MeshMembership.objects.create(node=in_scope_peer, tenant="tenant-a", site=site_a, is_enabled=True)
    MeshMembership.objects.create(node=disabled_peer, tenant="tenant-a", site=site_a, is_enabled=False)
    MeshMembership.objects.create(node=other_site_peer, tenant="tenant-a", site=site_b, is_enabled=True)

    for peer in (in_scope_peer, disabled_peer, other_site_peer):
        PeerPolicy.objects.create(
            tenant="tenant-a",
            site=site_a,
            source_node=caller,
            destination_node=peer,
            allowed_services=["telemetry"],
        )

    NodeEndpoint.objects.create(node=in_scope_peer, endpoint="wss://in-scope.example/ws", nat_type=NodeEndpoint.NatType.OPEN)
    NodeEndpoint.objects.create(node=disabled_peer, endpoint="wss://disabled.example/ws", nat_type=NodeEndpoint.NatType.OPEN)
    NodeEndpoint.objects.create(node=other_site_peer, endpoint="wss://other-site.example/ws", nat_type=NodeEndpoint.NatType.OPEN)

    response = client.get("/api/netmesh/peer-endpoints/", HTTP_AUTHORIZATION=f"Bearer {token}")

    assert response.status_code == 200
    assert [item["endpoint"] for item in response.json()["endpoints"]] == ["wss://in-scope.example/ws"]


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
