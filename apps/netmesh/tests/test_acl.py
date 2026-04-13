import pytest
from django.contrib.sites.models import Site

from apps.netmesh.models import MeshMembership, PeerPolicy
from apps.netmesh.services import ACLResolver
from apps.nodes.models import Node, NodeRole
from apps.ocpp.models import Charger


@pytest.mark.django_db
def test_acl_resolver_resolves_effective_allow_and_deny_for_service_identifier():
    gateway_role = NodeRole.objects.create(name="Gateway")
    service_role = NodeRole.objects.create(name="Service")
    source = Node.objects.create(hostname="acl-source", role=gateway_role, mesh_capability_flags=["edge", "routing"])
    destination = Node.objects.create(hostname="acl-destination", role=service_role, mesh_capability_flags=["ingress"])
    source_station = Charger.objects.create(charger_id="SRC-1", manager_node=source)

    MeshMembership.objects.create(node=source, tenant="tenant-acl", is_enabled=True)
    MeshMembership.objects.create(node=destination, tenant="tenant-acl", is_enabled=True)

    PeerPolicy.objects.create(
        tenant="tenant-acl",
        source_station=source_station,
        source_tags=["edge"],
        destination_group=service_role,
        destination_tags=["ingress"],
        allowed_services=["telemetry", "heartbeat"],
        denied_services=["heartbeat"],
    )

    resolver = ACLResolver(tenant="tenant-acl", site_id=None)

    summary = resolver.resolve_pair(source_node=source, destination_node=destination)

    assert summary.allowed_tasks == ["telemetry"]
    assert summary.denied_tasks == ["heartbeat"]
    assert resolver.resolve_service(source_node=source, destination_node=destination, service_identifier="telemetry")
    assert not resolver.resolve_service(source_node=source, destination_node=destination, service_identifier="heartbeat")


@pytest.mark.django_db
def test_acl_resolver_uses_scope_filters_for_tenant_and_site():
    site = Site.objects.create(domain="tenant-acl-site.example", name="Tenant ACL Site")
    source = Node.objects.create(hostname="scoped-source")
    destination = Node.objects.create(hostname="scoped-destination")
    other_site_destination = Node.objects.create(hostname="scoped-destination-other")

    MeshMembership.objects.create(node=source, tenant="tenant-scope", site=site, is_enabled=True)
    MeshMembership.objects.create(node=destination, tenant="tenant-scope", site=site, is_enabled=True)
    MeshMembership.objects.create(node=other_site_destination, tenant="tenant-scope", is_enabled=True)

    PeerPolicy.objects.create(
        tenant="tenant-scope",
        site=site,
        source_node=source,
        destination_node=destination,
        allowed_services=["ocpp"],
    )
    PeerPolicy.objects.create(
        tenant="tenant-scope",
        source_node=source,
        destination_node=other_site_destination,
        allowed_services=["should-not-match"],
    )

    resolver = ACLResolver(tenant="tenant-scope", site_id=site.id)

    summary = resolver.resolve_pair(source_node=source, destination_node=destination)
    other_summary = resolver.resolve_pair(source_node=source, destination_node=other_site_destination)

    assert summary.allowed_tasks == ["ocpp"]
    assert other_summary.allowed_tasks == []


@pytest.mark.django_db
def test_acl_resolver_ignores_policies_with_no_selectors():
    source = Node.objects.create(hostname="selectorless-source")
    destination = Node.objects.create(hostname="selectorless-destination")

    MeshMembership.objects.create(node=source, tenant="tenant-selectorless", is_enabled=True)
    MeshMembership.objects.create(node=destination, tenant="tenant-selectorless", is_enabled=True)

    PeerPolicy.objects.create(
        tenant="tenant-selectorless",
        allowed_services=["ssh"],
    )

    resolver = ACLResolver(tenant="tenant-selectorless", site_id=None)
    summary = resolver.resolve_pair(source_node=source, destination_node=destination)

    assert summary.policy_ids == []
    assert summary.allowed_tasks == []
    assert summary.denied_tasks == []
