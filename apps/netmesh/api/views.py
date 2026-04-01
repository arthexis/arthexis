"""Authenticated Netmesh API endpoints for node agent synchronization."""

from __future__ import annotations

import hashlib
import json

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.db.models import Q
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
    if_none_match = request.headers.get("If-None-Match", "")
    etag_tokens = [token.strip() for token in if_none_match.split(",") if token.strip()]
    if if_none_match == "*" or etag in etag_tokens:
        response = HttpResponse(status=304)
        response.headers["ETag"] = etag
        return response

    response = JsonResponse(payload)
    response.headers["ETag"] = etag
    return response


def _policies_for_caller(*, principal, filters):
    source_role = getattr(principal.node, "role", None)
    policy_filter = Q(source_node=principal.node)
    if source_role:
        policy_filter |= Q(source_group=source_role)
    return PeerPolicy.objects.filter(**filters).filter(policy_filter)


def _peer_ids_from_policies(*, policies, filters):
    destination_node_ids = set(
        policies.filter(destination_node__isnull=False).values_list("destination_node_id", flat=True).distinct(),
    )
    destination_group_ids = list(
        policies.filter(destination_group__isnull=False).values_list("destination_group_id", flat=True).distinct(),
    )
    if destination_group_ids:
        destination_node_ids.update(
            MeshMembership.objects.filter(
                **filters,
                is_enabled=True,
                node__role_id__in=destination_group_ids,
            ).values_list("node_id", flat=True),
        )
    return sorted(destination_node_ids)


def _membership_or_auth_error(request: HttpRequest):
    principal, error = authenticate_enrollment(request)
    if principal is None:
        return None, JsonResponse({"detail": error}, status=401)

    scope_membership = _scope_for_caller(node=principal.node, site_id=principal.site_id)
    if scope_membership is None:
        return None, JsonResponse({"detail": "caller has no active mesh membership"}, status=403)

    return (principal, scope_membership), None


def _policies_for_caller(*, principal, membership):
    filters = _scope_filters(membership=membership)
    source_role = getattr(principal.node, "role", None)
    policies = PeerPolicy.objects.filter(**filters, source_node=principal.node)
    if source_role:
        policies = policies | PeerPolicy.objects.filter(**filters, source_group=source_role)
    return policies.distinct()


def _destination_node_ids(*, policies, membership, principal) -> list[int]:
    destination_node_ids = set(
        policies.filter(destination_node__isnull=False)
        .order_by()
        .values_list("destination_node_id", flat=True)
    )

    destination_group_ids = list(
        policies.filter(destination_group__isnull=False).values_list("destination_group_id", flat=True).distinct()
    )
    if destination_group_ids:
        group_member_ids = MeshMembership.objects.filter(
            **_scope_filters(membership=membership),
            is_enabled=True,
            node__role_id__in=destination_group_ids,
        ).exclude(node=principal.node).order_by().values_list("node_id", flat=True)
        destination_node_ids.update(group_member_ids)

    return sorted(destination_node_ids)


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
    policies = _policies_for_caller(principal=principal, membership=membership)
    destination_node_ids = _destination_node_ids(
        policies=policies,
        membership=membership,
        principal=principal,
    )

    peer_memberships = (
        MeshMembership.objects.select_related("node", "node__role")
        .filter(**_scope_filters(membership=membership), is_enabled=True, node_id__in=destination_node_ids)
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
    policies = _policies_for_caller(principal=principal, membership=membership)
    policy_peer_ids = _destination_node_ids(
        policies=policies,
        membership=membership,
        principal=principal,
    )
    peer_ids = list(
        MeshMembership.objects.filter(
            **_scope_filters(membership=membership),
            is_enabled=True,
            node_id__in=policy_peer_ids,
        )
        .exclude(node=principal.node)
        .order_by()
        .values_list("node_id", flat=True)
    )
    endpoints_qs = list(NodeEndpoint.objects.filter(node_id__in=peer_ids).select_related("node", "node__role"))
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
    policies = _policies_for_caller(principal=principal, membership=membership)

    profile = _node_role_profile_name(principal.node)
    policies_list = list(policies.select_related("destination_node", "destination_group"))
    acl_rows = []
    for policy in policies_list:
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

    version = max([membership.id] + [policy.id for policy in policies_list])
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
