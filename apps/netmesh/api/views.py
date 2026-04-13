"""Authenticated Netmesh API endpoints for node agent synchronization."""

from __future__ import annotations

import hashlib
import json
import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.http import http_date
from django.views.decorators.http import require_GET

from apps.netmesh.api.auth import authenticate_enrollment
from apps.netmesh.api.serializers import serialize_active_transport_key
from apps.netmesh.metrics import map_generation_timer
from apps.netmesh.models import (
    MeshMembership,
    NodeKeyMaterial,
)
from apps.netmesh.services import ACLResolver
from utils.api_errors import json_api_error

logger = logging.getLogger("apps.netmesh.api")


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
    principal, error = authenticate_enrollment(request, required_scope="mesh:read")
    if principal is None:
        status, code, message = error
        return None, json_api_error(status=status, code=code, message=message)

    scope_membership = _scope_for_caller(node=principal.node, site_id=principal.site_id)
    if scope_membership is None:
        return None, json_api_error(
            status=403,
            code="mesh_membership_missing",
            message="caller has no active mesh membership",
        )

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
    resolver = ACLResolver(tenant=membership.tenant, site_id=membership.site_id)
    peer_memberships = MeshMembership.objects.select_related("node", "node__role").filter(**filters, is_enabled=True).exclude(
        node=principal.node
    )
    active_transport_key_by_node = {
        key.node_id: key
        for key in NodeKeyMaterial.objects.filter(
            node_id__in=peer_memberships.values_list("node_id", flat=True),
            key_state=NodeKeyMaterial.KeyState.ACTIVE,
            key_type=NodeKeyMaterial.KeyType.X25519,
        )
    }

    profile = _node_role_profile_name(principal.node)
    peers = []
    with map_generation_timer():
        for mesh_peer in peer_memberships:
            pair_summary = resolver.resolve_pair(source_node=principal.node, destination_node=mesh_peer.node)
            if not pair_summary.allowed_services:
                logger.info(
                    "Netmesh policy denied peer visibility",
                    extra={
                        "event": "netmesh.policy.denied",
                        "source_node_id": principal.node.id,
                        "destination_node_id": mesh_peer.node_id,
                        "policy_ids": pair_summary.policy_ids,
                        "denied_services": pair_summary.denied_services,
                    },
                )
                continue
            peer_payload = {
                "node_id": mesh_peer.node_id,
                "hostname": mesh_peer.node.hostname,
                "role": getattr(mesh_peer.node.role, "name", ""),
                "task_policy": {
                    "policy_ids": pair_summary.policy_ids,
                    "allowed_tasks": pair_summary.allowed_services,
                    "denied_tasks": pair_summary.denied_services,
                },
            }
            if profile in {"gateway", "service"}:
                peer_payload["tenant"] = mesh_peer.tenant
                peer_payload["site_id"] = mesh_peer.site_id
            peer_payload["transport_key"] = serialize_active_transport_key(
                key_material=active_transport_key_by_node.get(mesh_peer.node_id)
            )
            peers.append(peer_payload)

    payload = {
        "version": max(
            [membership.id] + [peer.id for peer in peer_memberships],
        ),
        "peers": peers,
    }
    logger.info(
        "Netmesh peer map generated",
        extra={
            "event": "netmesh.map.generated",
            "node_id": principal.node.id,
            "peer_count": len(peers),
            "map_type": "peers",
        },
    )
    return _json_with_etag(request, payload)


@require_GET
def acl_policy(request: HttpRequest) -> HttpResponse:
    resolved, error_response = _membership_or_auth_error(request)
    if error_response:
        return error_response

    principal, membership = resolved
    filters = _scope_filters(membership=membership)
    resolver = ACLResolver(tenant=membership.tenant, site_id=membership.site_id)
    peer_memberships = list(
        MeshMembership.objects.select_related("node", "node__role").filter(**filters, is_enabled=True).exclude(node=principal.node)
    )

    profile = _node_role_profile_name(principal.node)
    acl_rows = []
    for peer in peer_memberships:
        pair_summary = resolver.resolve_pair(source_node=principal.node, destination_node=peer.node)
        if not pair_summary.policy_ids:
            continue
        row = {
            "destination_node_id": peer.node_id,
            "allowed_tasks": pair_summary.allowed_services,
            "denied_tasks": pair_summary.denied_services,
            "policy_ids": pair_summary.policy_ids,
        }
        if profile in {"gateway", "service"}:
            row["destination_hostname"] = peer.node.hostname
        if profile == "gateway":
            row["tenant"] = membership.tenant
            row["site_id"] = membership.site_id
        acl_rows.append(row)

    policy_version = [policy_id for row in acl_rows for policy_id in row["policy_ids"]]
    version = max([membership.id] + policy_version) if policy_version else membership.id
    payload = {"version": version, "acl": acl_rows}
    return _json_with_etag(request, payload)


@require_GET
def key_info(request: HttpRequest) -> HttpResponse:
    resolved, error_response = _membership_or_auth_error(request)
    if error_response:
        return error_response

    principal, _membership = resolved
    active_key = (
        NodeKeyMaterial.objects.filter(
            node=principal.node,
            key_state=NodeKeyMaterial.KeyState.ACTIVE,
            key_type=NodeKeyMaterial.KeyType.X25519,
        )
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
                "type": active_key.key_type,
                "version": active_key.key_version,
                "created_at": http_date(active_key.created_at.timestamp()),
                "rotated_at": http_date(active_key.rotated_at.timestamp()) if active_key.rotated_at else None,
            },
        }
    return _json_with_etag(request, payload)
