"""Authenticated Netmesh API endpoints for node agent synchronization."""

from __future__ import annotations

import hashlib
import json

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.http import http_date
from django.views.decorators.http import require_GET

from apps.netmesh.api.auth import authenticate_enrollment
from apps.netmesh.models import MeshMembership, NodeEndpoint, NodeKeyMaterial, PeerPolicy, ServiceAdvertisement


def _node_role_profile_name(node) -> str:
    role_name = (getattr(getattr(node, "role", None), "name", "") or "").strip().lower()
    if "gateway" in role_name:
        return "gateway"
    if "service" in role_name:
        return "service"
    if "charger" in role_name or "terminal" in role_name:
        return "charger"
    return "service"


def _scope_for_caller(*, node, site_id: int | None):
    memberships = MeshMembership.objects.filter(node=node, is_enabled=True).select_related("site")
    if site_id:
        scoped_membership = memberships.filter(site_id=site_id).order_by("-pk").first()
        if scoped_membership:
            return scoped_membership
    return memberships.filter(site__isnull=True).order_by("-pk").first()


def _scope_filters(*, membership):
    filters = {"tenant": membership.tenant}
    if membership.site_id:
        filters["site_id"] = membership.site_id
    else:
        filters["site__isnull"] = True
    return filters


def _json_with_etag(request: HttpRequest, payload: dict) -> HttpResponse:
    rendered = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    etag = f'W/"{hashlib.sha256(rendered.encode("utf-8")).hexdigest()}"'
    if request.headers.get("If-None-Match") == etag:
        response = HttpResponse(status=304)
        response.headers["ETag"] = etag
        return response

    response = JsonResponse(payload)
    response.headers["ETag"] = etag
    return response


def _membership_or_auth_error(request: HttpRequest):
    principal, error = authenticate_enrollment(request)
    if principal is None:
        return None, JsonResponse({"detail": error}, status=401)

    scope_membership = _scope_for_caller(node=principal.node, site_id=principal.site_id)
    if scope_membership is None:
        return None, JsonResponse({"detail": "caller has no active mesh membership"}, status=403)

    return (principal, scope_membership), None


@require_GET
def caller_metadata(request: HttpRequest) -> HttpResponse:
    resolved, error_response = _membership_or_auth_error(request)
    if error_response:
        return error_response

    principal, membership = resolved
    payload = {
        "version": principal.enrollment.id,
        "node": {
            "id": principal.node.id,
            "hostname": principal.node.hostname,
            "public_endpoint": principal.node.public_endpoint,
            "role": getattr(principal.node.role, "name", ""),
            "profile": _node_role_profile_name(principal.node),
            "tenant": membership.tenant,
            "site_id": membership.site_id,
        },
    }
    return _json_with_etag(request, payload)


@require_GET
def permitted_peers(request: HttpRequest) -> HttpResponse:
    resolved, error_response = _membership_or_auth_error(request)
    if error_response:
        return error_response

    principal, membership = resolved
    filters = _scope_filters(membership=membership)

    source_role = getattr(principal.node, "role", None)
    policies = PeerPolicy.objects.filter(**filters).filter(
        source_node=principal.node,
    )
    if source_role:
        policies = policies | PeerPolicy.objects.filter(**filters).filter(source_group=source_role)

    destination_node_ids = sorted(
        {
            policy.destination_node_id
            for policy in policies.select_related("destination_node", "destination_group")
            if policy.destination_node_id
        }
    )

    peer_memberships = (
        MeshMembership.objects.select_related("node", "node__role")
        .filter(**filters, is_enabled=True, node_id__in=destination_node_ids)
        .exclude(node=principal.node)
    )

    profile = _node_role_profile_name(principal.node)
    peers = []
    for mesh_peer in peer_memberships:
        peer_payload = {
            "node_id": mesh_peer.node_id,
            "hostname": mesh_peer.node.hostname,
            "public_endpoint": mesh_peer.node.public_endpoint,
            "role": getattr(mesh_peer.node.role, "name", ""),
        }
        if profile in {"gateway", "service"}:
            peer_payload["tenant"] = mesh_peer.tenant
            peer_payload["site_id"] = mesh_peer.site_id
        peers.append(peer_payload)

    payload = {
        "version": max(
            [membership.id] + [peer.id for peer in peer_memberships],
        ),
        "peers": peers,
    }
    return _json_with_etag(request, payload)


@require_GET
def peer_endpoints(request: HttpRequest) -> HttpResponse:
    resolved, error_response = _membership_or_auth_error(request)
    if error_response:
        return error_response

    principal, membership = resolved
    filters = _scope_filters(membership=membership)

    peer_ids = list(
        MeshMembership.objects.filter(**filters, is_enabled=True).exclude(node=principal.node).values_list("node_id", flat=True)
    )
    endpoints_qs = NodeEndpoint.objects.filter(node_id__in=peer_ids).select_related("node", "node__role")
    ads_qs = ServiceAdvertisement.objects.filter(node_id__in=peer_ids)
    ads_by_node: dict[int, list[dict]] = {}
    for advertisement in ads_qs:
        ads_by_node.setdefault(advertisement.node_id, []).append(
            {
                "service": advertisement.service_name,
                "port": advertisement.port,
                "protocol": advertisement.protocol,
            }
        )

    profile = _node_role_profile_name(principal.node)
    endpoints = []
    for endpoint in endpoints_qs:
        row = {
            "node_id": endpoint.node_id,
            "endpoint": endpoint.endpoint,
            "last_seen": endpoint.last_seen.isoformat() if endpoint.last_seen else None,
        }
        if profile == "gateway":
            row["nat_type"] = endpoint.nat_type
            row["services"] = ads_by_node.get(endpoint.node_id, [])
        endpoints.append(row)

    version = [membership.id]
    version.extend(endpoint.id for endpoint in endpoints_qs)
    payload = {"version": max(version), "endpoints": endpoints}
    return _json_with_etag(request, payload)


@require_GET
def acl_policy(request: HttpRequest) -> HttpResponse:
    resolved, error_response = _membership_or_auth_error(request)
    if error_response:
        return error_response

    principal, membership = resolved
    filters = _scope_filters(membership=membership)
    source_role = getattr(principal.node, "role", None)
    policies = PeerPolicy.objects.filter(**filters).filter(source_node=principal.node)
    if source_role:
        policies = policies | PeerPolicy.objects.filter(**filters).filter(source_group=source_role)

    profile = _node_role_profile_name(principal.node)
    acl_rows = []
    for policy in policies.select_related("destination_node", "destination_group"):
        row = {
            "policy_id": policy.id,
            "allowed_services": policy.allowed_services,
        }
        if policy.destination_node_id:
            row["destination_node_id"] = policy.destination_node_id
            row["destination_endpoint"] = policy.destination_node.public_endpoint
        elif profile in {"gateway", "service"} and policy.destination_group_id:
            row["destination_group"] = policy.destination_group.name
        if profile == "gateway":
            row["tenant"] = policy.tenant
            row["site_id"] = policy.site_id
        acl_rows.append(row)

    version = max([membership.id] + [policy.id for policy in policies])
    payload = {"version": version, "acl": acl_rows}
    return _json_with_etag(request, payload)


@require_GET
def key_info(request: HttpRequest) -> HttpResponse:
    resolved, error_response = _membership_or_auth_error(request)
    if error_response:
        return error_response

    principal, _membership = resolved
    active_key = (
        NodeKeyMaterial.objects.filter(node=principal.node, revoked=False)
        .order_by("-created_at", "-id")
        .first()
    )
    if active_key is None:
        payload = {
            "version": principal.enrollment.id,
            "key": {
                "state": "missing",
            },
        }
    else:
        payload = {
            "version": max(principal.enrollment.id, active_key.id),
            "key": {
                "state": "active",
                "fingerprint": hashlib.sha256(active_key.public_key.encode("utf-8")).hexdigest()[:16],
                "created_at": http_date(active_key.created_at.timestamp()),
                "rotated_at": http_date(active_key.rotated_at.timestamp()) if active_key.rotated_at else None,
            },
        }
    return _json_with_etag(request, payload)
