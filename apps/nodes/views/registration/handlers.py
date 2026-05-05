"""Registration view handlers and orchestration helpers."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import serialization
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.sites.models import Site
from django.db import IntegrityError
from django.http import JsonResponse
from django.test.client import RequestFactory
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.nodes.logging import get_register_visitor_logger
from apps.nodes.models import Node, NodeRole, node_information_updated
from apps.nodes.services.enrollment import submit_public_key
from config.request_utils import is_https_request
from utils.api import api_login_required

from .auth import (
    _enforce_authentication,
    _verify_signature,
    allow_signature_failure_with_authenticated_user,
    ensure_authenticated_user,
)
from .cors import add_cors_headers
from .network import (
    HostNameSSLAdapter,
    _get_host_domain,
    _get_host_ip,
    _get_host_port,
    append_token,
    get_advertised_address,
    get_client_ip,
    get_public_targets,
    iter_port_fallback_urls,
)
from .network_utils import _get_route_address
from .payload import (
    NodeRegistrationPayload,
    parse_registration_request,
    validate_payload,
)
from .policy import is_allowed_visitor_url
from .sanitization import (
    redact_mac,
    redact_network_value,
    redact_token_value,
    redact_url_token,
)

logger = logging.getLogger("apps.nodes.views")
registration_logger = get_register_visitor_logger()


def _extract_response_detail(response) -> str:
    """Extract detail text from JSON and non-JSON responses."""

    try:
        decoded_body = response.content.decode()
    except UnicodeDecodeError:
        return ""

    try:
        payload = json.loads(decoded_body)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, Mapping) and payload.get("detail"):
        return str(payload["detail"])
    return decoded_body


def _parse_json_response_mapping(response) -> Mapping[str, object]:
    """Parse a JSON response body as a mapping."""

    payload = json.loads(response.content.decode() or "{}")
    if not isinstance(payload, Mapping):
        raise ValueError("expected JSON object")
    return payload


def _sign_token_for_node(data: dict[str, object], node: Node, token: str):
    """Attach token signature to node info payload when signing succeeds."""

    if not token:
        return

    priv_path = node.get_base_path() / "security" / f"{node.public_endpoint}"
    try:
        private_key_bytes = priv_path.read_bytes()
    except (FileNotFoundError, OSError) as exc:
        registration_logger.warning(
            "Visitor registration: unable to read signing key for %s",
            node.public_endpoint,
            extra={
                "target": str(priv_path),
                "attempt": "key_read",
                "exception_class": exc.__class__.__name__,
            },
        )
        return

    try:
        private_key = serialization.load_pem_private_key(
            private_key_bytes, password=None
        )
    except (TypeError, ValueError) as exc:
        registration_logger.warning(
            "Visitor registration: unable to parse signing key for %s",
            node.public_endpoint,
            extra={
                "target": str(priv_path),
                "attempt": "key_parse",
                "exception_class": exc.__class__.__name__,
            },
        )
        return
    except Exception as exc:
        registration_logger.warning(
            "Visitor registration: crypto error loading key for %s",
            node.public_endpoint,
            extra={
                "target": str(priv_path),
                "attempt": "key_crypto",
                "exception_class": exc.__class__.__name__,
            },
        )
        return

    try:
        signature, error = Node.sign_payload(token, private_key)
    except Exception as exc:
        registration_logger.warning(
            "Visitor registration: unable to sign token for %s",
            node.public_endpoint,
            extra={
                "target": node.public_endpoint,
                "attempt": "token_sign",
                "exception_class": exc.__class__.__name__,
            },
        )
        return

    if signature:
        data["token_signature"] = signature
        return
    if error:
        registration_logger.warning(
            "Visitor registration: unable to sign token for %s: %s",
            node.public_endpoint,
            error,
            extra={
                "target": node.public_endpoint,
                "attempt": "token_sign",
                "exception_class": "SignPayloadError",
            },
        )


@api_login_required
def node_list(request):
    """Return a JSON list of all known nodes."""

    nodes = [
        {
            "hostname": node.hostname,
            "network_hostname": node.network_hostname,
            "address": node.address,
            "ipv4_address": node.ipv4_address,
            "ipv6_address": node.ipv6_address,
            "port": node.port,
            "last_updated": node.last_updated,
            "features": list(node.features.values_list("slug", flat=True)),
            "installed_version": node.installed_version,
            "installed_revision": node.installed_revision,
            "mesh_enrollment_state": node.mesh_enrollment_state,
            "mesh_key_fingerprint_metadata": node.mesh_key_fingerprint_metadata,
            "last_mesh_heartbeat": node.last_mesh_heartbeat,
            "mesh_capability_flags": node.mesh_capability_flags,
        }
        for node in Node.objects.prefetch_related("features")
    ]
    return JsonResponse({"nodes": nodes})


@csrf_exempt
def node_info(request):
    """Return local node info and optional token signature."""

    node = Node.get_local()
    if node is None:
        node, _ = Node.register_current()

    token = request.GET.get("token", "")
    registration_logger.info(
        "Visitor registration: node_info requested token=%s client_ip=%s host_ip=%s",
        "present" if token else "absent",
        get_client_ip(request) or "",
        _get_host_ip(request) or "",
    )
    host_domain = _get_host_domain(request)
    advertised_address = get_advertised_address(request, node)
    preferred_port = node.get_preferred_port()
    advertised_port = node.port or preferred_port
    base_domain = node.get_base_domain()
    base_site_profile = getattr(node.base_site, "profile", None)
    base_site_requires_https = bool(getattr(base_site_profile, "require_https", False))
    if base_domain:
        advertised_port = node._preferred_site_port(True)
    if host_domain and not base_domain:
        host_port = _get_host_port(request)
        if host_port in {preferred_port, node.port, 80, 443}:
            advertised_port = host_port
        else:
            advertised_port = preferred_port
    if base_domain:
        hostname = base_domain
        address = base_domain
    elif host_domain:
        hostname = host_domain
        local_aliases = {
            value
            for value in (
                node.hostname,
                node.network_hostname,
                node.address,
                node.public_endpoint,
            )
            if value
        }
        if advertised_address and advertised_address not in local_aliases:
            address = advertised_address
        else:
            address = host_domain
    else:
        hostname = node.get_preferred_hostname()
        address = advertised_address or node.address or node.network_hostname or ""

    data = {
        "hostname": hostname,
        "network_hostname": node.network_hostname,
        "address": address,
        "ipv4_address": node.ipv4_address,
        "ipv6_address": node.ipv6_address,
        "port": advertised_port,
        "mac_address": node.mac_address,
        "public_key": node.public_key,
        "features": list(node.features.values_list("slug", flat=True)),
        "role": node.role.name if node.role_id else "",
        "contact_hosts": node.get_remote_host_candidates(),
        "installed_version": node.installed_version,
        "installed_revision": node.installed_revision,
        "mesh_enrollment_state": node.mesh_enrollment_state,
        "mesh_key_fingerprint_metadata": node.mesh_key_fingerprint_metadata,
        "last_mesh_heartbeat": node.last_mesh_heartbeat,
        "mesh_capability_flags": node.mesh_capability_flags,
        "base_site_domain": base_domain,
        "base_site_requires_https": base_site_requires_https,
        "request_is_https": is_https_request(request),
        "sibling_ipc": node.get_sibling_ipc_status(),
    }

    _sign_token_for_node(data, node, token)

    response = JsonResponse(data)
    response["Access-Control-Allow-Origin"] = "*"
    registration_logger.info(
        "Visitor registration: node_info response hostname=%s address=%s port=%s role=%s",
        redact_network_value(hostname),
        redact_network_value(address),
        advertised_port or "",
        getattr(node.role, "name", ""),
    )
    return response


def _normalize_addresses(payload: NodeRegistrationPayload):
    """Normalize MAC and IP address values for persistence."""

    mac_address = payload.mac_address.lower()
    address_value = payload.address or ""
    ipv6_value = payload.ipv6_address or ""
    ipv4_candidates = list(payload.ipv4_candidates)
    for candidate in Node.sanitize_ipv4_addresses(
        [payload.address, payload.network_hostname, payload.hostname]
    ):
        if candidate not in ipv4_candidates:
            ipv4_candidates.append(candidate)
    ipv4_value = Node.serialize_ipv4_addresses(ipv4_candidates) or ""

    for candidate in (payload.address, payload.network_hostname, payload.hostname):
        candidate = (candidate or "").strip()
        if not candidate:
            continue
        try:
            parsed_ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if parsed_ip.version == 6 and not ipv6_value:
            ipv6_value = str(parsed_ip)
    return mac_address, address_value, ipv6_value, ipv4_value


def _resolve_role(role_name: str, *, can_assign: bool):
    """Resolve requested role only when assignment is authorized."""

    if not (role_name and can_assign):
        return None
    return NodeRole.objects.filter(name=role_name).first()


def _update_features(node: Node, features, *, allow_update: bool):
    """Update node feature list from payload when permitted."""

    if features is None or not allow_update:
        return
    if isinstance(features, (str, bytes)):
        feature_list = [features]
    else:
        feature_list = list(features)
    node.update_manual_features(feature_list)


def _refresh_last_updated(node: Node, update_fields: list[str]):
    """Ensure ``last_updated`` is present in update fields."""

    node.last_updated = timezone.now()
    if "last_updated" not in update_fields:
        update_fields.append("last_updated")


def _log_registration_event(
    status: str,
    payload: NodeRegistrationPayload,
    request,
    *,
    detail: str | None = None,
    level: int = logging.INFO,
):
    """Record registration lifecycle logs with redacted identifiers."""

    registration_logger.log(
        level,
        "Node registration %s: hostname=%s mac_redacted=%s relation=%s client_ip=%s host_ip=%s detail=%s",
        status,
        payload.hostname or "<unknown>",
        redact_mac(payload.mac_address) or "<unknown>",
        payload.relation_value or "unspecified",
        get_client_ip(request) or "",
        _get_host_ip(request) or "",
        detail or "",
    )


def _deactivate_user_if_requested(request, deactivate_user: bool):
    """Deactivate temporary credentials when payload requests it."""

    if not deactivate_user:
        return
    deactivate = getattr(request.user, "deactivate_temporary_credentials", None)
    if callable(deactivate):
        deactivate()


def _is_self_host_conflict_error(
    error: IntegrityError,
    *,
    relation_value: Node.Relation | None,
    host_instance_id: str,
) -> bool:
    """Return True when a write failed due to SELF host uniqueness conflict."""

    if relation_value != Node.Relation.SELF:
        return False
    if not (host_instance_id or "").strip():
        return False
    message = str(error)
    return (
        "nodes_node_self_host_instance_unique" in message
        or "host_instance_id" in message
    )


def _update_existing_node(
    node: Node,
    *,
    payload: NodeRegistrationPayload,
    relation_value: Node.Relation | None,
    address_value: str,
    ipv4_value: str,
    ipv6_value: str,
    verified: bool,
    desired_role,
    trusted_allowed: bool,
    base_site: Site | None,
    request,
):
    """Update an existing node while preserving response compatibility."""

    previous_version = (node.installed_version or "").strip()
    previous_revision = (node.installed_revision or "").strip()
    update_fields: list[str] = []
    for field, value in (
        ("hostname", payload.hostname),
        ("network_hostname", payload.network_hostname),
        ("address", address_value),
        ("ipv4_address", ipv4_value),
        ("ipv6_address", ipv6_value),
        ("mac_address", payload.mac_address.lower()),
        ("host_instance_id", payload.host_instance_id),
        ("port", payload.port),
    ):
        current = getattr(node, field)
        if isinstance(value, str):
            value = value or ""
            current = current or ""
        if current != value:
            setattr(node, field, value)
            update_fields.append(field)

    if verified:
        node.public_key = payload.public_key
        update_fields.append("public_key")
    if payload.installed_version is not None:
        node.installed_version = str(payload.installed_version)[:20]
        if "installed_version" not in update_fields:
            update_fields.append("installed_version")
    if payload.installed_revision is not None:
        node.installed_revision = str(payload.installed_revision)[:40]
        if "installed_revision" not in update_fields:
            update_fields.append("installed_revision")
    if (
        payload.mesh_enrollment_state is not None
        and node.mesh_enrollment_state != payload.mesh_enrollment_state
    ):
        node.mesh_enrollment_state = payload.mesh_enrollment_state
        update_fields.append("mesh_enrollment_state")
    if (
        payload.mesh_key_fingerprint_metadata
        and node.mesh_key_fingerprint_metadata != payload.mesh_key_fingerprint_metadata
    ):
        node.mesh_key_fingerprint_metadata = payload.mesh_key_fingerprint_metadata
        update_fields.append("mesh_key_fingerprint_metadata")
    if (
        payload.last_mesh_heartbeat is not None
        and node.last_mesh_heartbeat != payload.last_mesh_heartbeat
    ):
        node.last_mesh_heartbeat = payload.last_mesh_heartbeat
        update_fields.append("last_mesh_heartbeat")
    if (
        payload.mesh_capability_flags
        and node.mesh_capability_flags != payload.mesh_capability_flags
    ):
        node.mesh_capability_flags = payload.mesh_capability_flags
        update_fields.append("mesh_capability_flags")
    if relation_value is not None and node.current_relation != relation_value:
        node.current_relation = relation_value
        update_fields.append("current_relation")
    if desired_role and node.role_id != desired_role.id:
        node.role = desired_role
        update_fields.append("role")
    if trusted_allowed and not node.trusted:
        node.trusted = True
        update_fields.append("trusted")
    if node.reserved:
        node.reserved = False
        update_fields.append("reserved")
    if base_site and node.base_site_id != base_site.id:
        node.base_site = base_site
        update_fields.append("base_site")

    _refresh_last_updated(node, update_fields)
    if update_fields:
        node.save(update_fields=update_fields)

    node_information_updated.send(
        sender=Node,
        node=node,
        previous_version=previous_version,
        previous_revision=previous_revision,
        current_version=(node.installed_version or "").strip(),
        current_revision=(node.installed_revision or "").strip(),
        request=request,
    )
    _update_features(
        node, payload.features, allow_update=verified or request.user.is_authenticated
    )
    _deactivate_user_if_requested(request, payload.deactivate_user)
    return JsonResponse(
        {
            "id": node.id,
            "uuid": str(node.uuid),
            "detail": f"Node already exists (id: {node.id})",
        }
    )


def _find_reserved_node_for_payload(
    payload: NodeRegistrationPayload,
    *,
    address_value: str,
    ipv4_value: str,
) -> Node | None:
    """Return a reserved placeholder that matches a first-contact payload."""

    hostname = (payload.hostname or "").strip()
    address_tokens = {
        token
        for raw_value in (address_value, ipv4_value, payload.network_hostname)
        for token in re.split(r"[\s,]+", raw_value or "")
        if token
    }
    queryset = Node.objects.filter(reserved=True)
    if hostname:
        node = queryset.filter(hostname__iexact=hostname).first()
        if node:
            return node
    for node in queryset.only("id", "address", "ipv4_address", "network_hostname"):
        node_tokens = {
            token
            for raw_value in (node.address, node.ipv4_address, node.network_hostname)
            for token in re.split(r"[\s,]+", raw_value or "")
            if token
        }
        if node_tokens & address_tokens:
            return node
    return None


@csrf_exempt
def register_node(request):
    """Register or update a node from POSTed data."""

    registration_logger.info(
        "Visitor registration: register_node called method=%s path=%s client_ip=%s host_ip=%s",
        request.method,
        request.path,
        get_client_ip(request) or "",
        _get_host_ip(request) or "",
    )
    if request.method == "OPTIONS":
        return add_cors_headers(request, JsonResponse({"detail": "ok"}))
    if request.method != "POST":
        return add_cors_headers(
            request, JsonResponse({"detail": "POST required"}, status=400)
        )

    ensure_authenticated_user(request)
    dto = parse_registration_request(request)
    payload = dto.payload

    _log_registration_event("attempt", payload, request)

    validation = validate_payload(payload)
    validation_response = validation.to_response()
    if validation_response:
        _log_registration_event(
            "failed", payload, request, detail=validation.detail, level=logging.WARNING
        )
        return add_cors_headers(request, validation_response)

    verified, signature_error = _verify_signature(payload)
    if allow_signature_failure_with_authenticated_user(request, signature_error):
        verified = False
        signature_error = None

    if signature_error:
        _log_registration_event(
            "failed",
            payload,
            request,
            detail=_extract_response_detail(signature_error),
            level=logging.WARNING,
        )
        return add_cors_headers(request, signature_error)

    auth_error = _enforce_authentication(request, verified=verified)
    if auth_error:
        _log_registration_event(
            "denied",
            payload,
            request,
            detail=_extract_response_detail(auth_error),
            level=logging.WARNING,
        )
        return add_cors_headers(request, auth_error)

    mac_address, address_value, ipv6_value, ipv4_value = _normalize_addresses(payload)
    trusted_allowed = bool(payload.trusted_requested) and (
        verified or request.user.is_authenticated
    )
    desired_role = _resolve_role(
        payload.role_name, can_assign=verified or request.user.is_authenticated
    )
    base_site = (
        Site.objects.filter(domain__iexact=payload.base_site_domain).first()
        if payload.base_site_domain
        else None
    )
    existing_node = Node.objects.filter(mac_address=mac_address).first()
    if existing_node is None:
        existing_node = _find_reserved_node_for_payload(
            payload,
            address_value=address_value,
            ipv4_value=ipv4_value,
        )
    relation_value = payload.relation_value
    if relation_value == Node.Relation.SELF and payload.host_instance_id:
        other_self_exists = (
            Node.objects.filter(
                current_relation=Node.Relation.SELF,
                host_instance_id=payload.host_instance_id,
            )
            .exclude(mac_address=mac_address)
            .exists()
        )
        if other_self_exists:
            relation_value = Node.Relation.SIBLING

    if payload.enrollment_token and payload.public_key:
        if existing_node is None:
            _log_registration_event(
                "failed",
                payload,
                request,
                detail="Invalid enrollment token",
                level=logging.WARNING,
            )
            return add_cors_headers(
                request,
                JsonResponse({"detail": "Invalid enrollment token"}, status=400),
            )
        enrollment_site = base_site or existing_node.base_site
        _, enrollment_error = submit_public_key(
            node=existing_node,
            token=payload.enrollment_token,
            public_key=payload.public_key,
            site=enrollment_site,
        )
        if enrollment_error:
            _log_registration_event(
                "failed",
                payload,
                request,
                detail=enrollment_error,
                level=logging.WARNING,
            )
            return add_cors_headers(
                request,
                JsonResponse({"detail": enrollment_error}, status=400),
            )

    defaults = {
        "hostname": payload.hostname,
        "network_hostname": payload.network_hostname,
        "address": address_value,
        "ipv4_address": ipv4_value,
        "ipv6_address": ipv6_value,
        "host_instance_id": payload.host_instance_id,
        "port": payload.port,
    }
    if trusted_allowed:
        defaults["trusted"] = True
    if desired_role:
        defaults["role"] = desired_role
    if verified:
        defaults["public_key"] = payload.public_key
    if base_site:
        defaults["base_site"] = base_site
    if payload.installed_version is not None:
        defaults["installed_version"] = str(payload.installed_version)[:20]
    if payload.installed_revision is not None:
        defaults["installed_revision"] = str(payload.installed_revision)[:40]
    if payload.mesh_enrollment_state is not None:
        defaults["mesh_enrollment_state"] = payload.mesh_enrollment_state
    if payload.mesh_key_fingerprint_metadata:
        defaults["mesh_key_fingerprint_metadata"] = (
            payload.mesh_key_fingerprint_metadata
        )
    if payload.last_mesh_heartbeat is not None:
        defaults["last_mesh_heartbeat"] = payload.last_mesh_heartbeat
    if payload.mesh_capability_flags:
        defaults["mesh_capability_flags"] = payload.mesh_capability_flags
    if relation_value is not None:
        defaults["current_relation"] = relation_value

    if existing_node is not None:
        node = existing_node
        created = False
    else:
        try:
            node, created = Node.objects.get_or_create(
                mac_address=mac_address,
                defaults=defaults,
            )
        except IntegrityError as error:
            if not _is_self_host_conflict_error(
                error,
                relation_value=relation_value,
                host_instance_id=payload.host_instance_id,
            ):
                raise
            relation_value = Node.Relation.SIBLING
            defaults["current_relation"] = relation_value
            node, created = Node.objects.get_or_create(
                mac_address=mac_address,
                defaults=defaults,
            )
    if not created:
        try:
            response = _update_existing_node(
                node,
                payload=payload,
                relation_value=relation_value,
                address_value=address_value,
                ipv4_value=ipv4_value,
                ipv6_value=ipv6_value,
                verified=verified,
                desired_role=desired_role,
                trusted_allowed=trusted_allowed,
                base_site=base_site,
                request=request,
            )
        except IntegrityError as error:
            if not _is_self_host_conflict_error(
                error,
                relation_value=relation_value,
                host_instance_id=payload.host_instance_id,
            ):
                raise
            response = _update_existing_node(
                node,
                payload=payload,
                relation_value=Node.Relation.SIBLING,
                address_value=address_value,
                ipv4_value=ipv4_value,
                ipv6_value=ipv6_value,
                verified=verified,
                desired_role=desired_role,
                trusted_allowed=trusted_allowed,
                base_site=base_site,
                request=request,
            )
        _log_registration_event(
            "succeeded", payload, request, detail=f"updated node {node.id}"
        )
        return add_cors_headers(request, response)

    _update_features(
        node, payload.features, allow_update=verified or request.user.is_authenticated
    )
    node_information_updated.send(
        sender=Node,
        node=node,
        previous_version="",
        previous_revision="",
        current_version=(node.installed_version or "").strip(),
        current_revision=(node.installed_revision or "").strip(),
        request=request,
    )
    _deactivate_user_if_requested(request, payload.deactivate_user)
    response = JsonResponse({"id": node.id, "uuid": str(node.uuid)})
    _log_registration_event(
        "succeeded", payload, request, detail=f"created node {node.id}"
    )
    return add_cors_headers(request, response)


@csrf_exempt
@require_POST
def submit_enrollment_public_key(request):
    """Accept one-time enrollment token and node public key submission."""

    dto = parse_registration_request(request)
    payload = dto.payload
    mac_address = (payload.mac_address or "").strip().lower()
    if not mac_address or not payload.enrollment_token or not payload.public_key:
        return JsonResponse(
            {"detail": ("mac_address, enrollment_token, and public_key are required")},
            status=400,
        )

    node = Node.objects.filter(mac_address=mac_address).first()
    if not node:
        return JsonResponse({"detail": "unknown node"}, status=404)

    site = (
        Site.objects.filter(domain__iexact=payload.base_site_domain).first()
        if payload.base_site_domain
        else node.base_site
    )
    _, error = submit_public_key(
        node=node,
        token=payload.enrollment_token,
        public_key=payload.public_key,
        site=site,
    )
    if error:
        return JsonResponse({"detail": error}, status=400)
    return JsonResponse(
        {
            "detail": "public key accepted; enrollment pending approval",
            "mesh_enrollment_state": node.mesh_enrollment_state,
        }
    )


def _build_registration_payload(
    info: Mapping[str, object] | None, relation: str | None
):
    """Build host/visitor relay payload preserving legacy fields."""

    payload = {
        "hostname": info.get("hostname") if info else "",
        "address": info.get("address") if info else "",
        "port": info.get("port") if info else None,
        "mac_address": info.get("mac_address") if info else "",
        "public_key": info.get("public_key") if info else "",
        "features": info.get("features") if info else [],
        "trusted": True,
    }
    if info and not payload["address"]:
        payload["address"] = info.get("network_hostname") or ""
    base_site_domain = info.get("base_site_domain") if info else ""
    if isinstance(base_site_domain, str) and base_site_domain.strip():
        payload["base_site_domain"] = base_site_domain.strip()
    relation_value = relation or (info.get("current_relation") if info else None)
    if relation_value:
        payload["current_relation"] = relation_value
    for key in (
        "mesh_enrollment_state",
        "mesh_key_fingerprint_metadata",
        "last_mesh_heartbeat",
        "mesh_capability_flags",
    ):
        if info and key in info:
            payload[key] = info[key]
    if info:
        role_value = ""
        for candidate in (info.get("role"), info.get("role_name")):
            if isinstance(candidate, str) and candidate.strip():
                role_value = candidate.strip()
                break
        if role_value:
            payload["role"] = role_value
    return payload


def _apply_token_signature(
    payload: dict, info: Mapping[str, object] | None, token: str
):
    """Copy token signature from info payload when present."""

    if info and token and info.get("token_signature"):
        payload["token"] = token
        payload["signature"] = info.get("token_signature")


def _try_proxy_json_request(
    *,
    session: requests.Session,
    url: str,
    timeout_seconds: int,
    method: str,
    log_prefix: str,
    request_error_message: str,
    response_error_message: str,
    payload: Mapping[str, object] | None = None,
):
    """Attempt proxied JSON request across candidate URLs and public targets."""

    body = None
    attempt = 0
    last_error: Exception | None = None
    selected_url = url

    for candidate in iter_port_fallback_urls(url):
        for target in get_public_targets(candidate):
            attempt += 1
            try:
                parsed_target = urlsplit(target.url)
                session.mount(
                    f"{parsed_target.scheme}://{parsed_target.netloc}",
                    HostNameSSLAdapter(target.server_hostname),
                )
                if method == "post":
                    response = session.post(
                        target.url,
                        json=payload,
                        headers={"Host": target.host_header},
                        timeout=timeout_seconds,
                    )
                else:
                    response = session.get(
                        target.url,
                        headers={"Host": target.host_header},
                        timeout=timeout_seconds,
                    )
                response.raise_for_status()
                parsed_body = response.json()
                if not isinstance(parsed_body, Mapping):
                    raise ValueError("expected JSON object")
            except ValueError as exc:
                last_error = exc
                registration_logger.warning(
                    "%s: %s",
                    log_prefix,
                    response_error_message,
                    extra={
                        "target": redact_url_token(target.url),
                        "attempt": attempt,
                        "exception_class": exc.__class__.__name__,
                    },
                )
                continue
            except requests.exceptions.RequestException as exc:
                last_error = exc
                registration_logger.warning(
                    "%s: %s",
                    log_prefix,
                    request_error_message,
                    extra={
                        "target": redact_url_token(target.url),
                        "attempt": attempt,
                        "exception_class": exc.__class__.__name__,
                    },
                )
                continue
            body = parsed_body
            selected_url = candidate
            return body, selected_url, last_error, attempt

    return body, selected_url, last_error, attempt


@staff_member_required
@require_POST
def register_visitor_proxy(request):
    """Proxy visitor registration handshake from server side."""

    try:
        data = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    visitor_info_url = str(data.get("visitor_info_url") or "").strip()
    visitor_register_url = str(data.get("visitor_register_url") or "").strip()
    token = str(data.get("token") or "").strip()

    if not visitor_info_url or not visitor_register_url:
        return JsonResponse(
            {"detail": "visitor info/register URLs required"}, status=400
        )
    if not is_allowed_visitor_url(visitor_info_url) or not is_allowed_visitor_url(
        visitor_register_url
    ):
        return JsonResponse({"detail": "invalid visitor info/register URL"}, status=400)
    if not (
        get_public_targets(visitor_info_url)
        and get_public_targets(visitor_register_url)
    ):
        return JsonResponse(
            {"detail": "visitor info/register URL must resolve to a public IP address"},
            status=400,
        )

    try:
        visitor_info_url = append_token(visitor_info_url, token)
        factory = RequestFactory()
        host_info_request = factory.get(
            "/nodes/info/", {"token": token} if token else {}
        )
        host_info_request.user = request.user
        host_info_request._cached_user = request.user
        try:
            host_info = _parse_json_response_mapping(node_info(host_info_request))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return JsonResponse({"detail": "host info unavailable"}, status=502)

        session = requests.Session()
        timeout_seconds = 45

        try:
            visitor_info, visitor_info_url, last_error, info_attempt = (
                _try_proxy_json_request(
                    session=session,
                    url=visitor_info_url,
                    timeout_seconds=timeout_seconds,
                    method="get",
                    log_prefix="Visitor registration proxy",
                    request_error_message="info request failed",
                    response_error_message="info response json parse failed",
                )
            )
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            registration_logger.warning(
                "Visitor registration proxy: unexpected visitor info proxy failure",
                extra={
                    "target": redact_url_token(visitor_info_url),
                    "attempt": "visitor_info_proxy",
                    "exception_class": exc.__class__.__name__,
                },
            )
            return JsonResponse({"detail": "visitor info unavailable"}, status=502)
        if visitor_info is None:
            registration_logger.warning(
                "Visitor registration proxy: unable to fetch visitor info from %s: %s",
                redact_url_token(visitor_info_url),
                last_error,
                extra={
                    "target": redact_url_token(visitor_info_url),
                    "attempt": info_attempt,
                    "exception_class": (
                        last_error.__class__.__name__ if last_error else ""
                    ),
                },
            )
            return JsonResponse({"detail": "visitor info unavailable"}, status=502)

        host_payload = _build_registration_payload(visitor_info, "Downstream")
        _apply_token_signature(host_payload, visitor_info, token)
        host_register_request = factory.post(
            "/nodes/register/",
            data=json.dumps(host_payload),
            content_type="application/json",
        )
        host_register_request.user = request.user
        host_register_request._cached_user = request.user
        host_register_response = register_node(host_register_request)
        try:
            host_register_body = _parse_json_response_mapping(host_register_response)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return JsonResponse({"detail": "host registration failed"}, status=502)
        if host_register_response.status_code != 200 or not host_register_body.get(
            "id"
        ):
            status_code = host_register_response.status_code or 400
            if 200 <= status_code < 300:
                status_code = 400
            return JsonResponse(
                {"detail": "host registration failed"},
                status=status_code,
            )

        visitor_payload = _build_registration_payload(host_info, "Upstream")
        _apply_token_signature(visitor_payload, host_info, token)

        try:
            visitor_register_body, visitor_register_url, last_error, register_attempt = (
                _try_proxy_json_request(
                    session=session,
                    url=visitor_register_url,
                    timeout_seconds=timeout_seconds,
                    method="post",
                    payload=visitor_payload,
                    log_prefix="Visitor registration proxy",
                    request_error_message="visitor notification request failed",
                    response_error_message="visitor response json parse failed",
                )
            )
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            registration_logger.warning(
                "Visitor registration proxy: unexpected visitor confirmation proxy failure",
                extra={
                    "target": redact_url_token(visitor_register_url),
                    "attempt": "visitor_register_proxy",
                    "exception_class": exc.__class__.__name__,
                },
            )
            return JsonResponse({"detail": "visitor confirmation failed"}, status=502)
        if visitor_register_body is None:
            registration_logger.warning(
                "Visitor registration proxy: unable to notify visitor at %s: %s",
                redact_url_token(visitor_register_url),
                last_error,
                extra={
                    "target": redact_url_token(visitor_register_url),
                    "attempt": register_attempt,
                    "exception_class": (
                        last_error.__class__.__name__ if last_error else ""
                    ),
                },
            )
            return JsonResponse({"detail": "visitor confirmation failed"}, status=502)

        visitor_id = visitor_register_body.get("id")
        visitor_detail = (
            "visitor confirmation accepted"
            if visitor_id
            else "visitor confirmation failed"
        )

        return JsonResponse(
            {
                "host": {
                    "detail": "host registration accepted",
                    "id": host_register_body.get("id"),
                },
                "visitor": {
                    "detail": visitor_detail,
                    "id": visitor_id,
                },
                "host_requires_https": bool(host_info.get("base_site_requires_https")),
                "visitor_requires_https": bool(
                    visitor_info.get("base_site_requires_https")
                ),
            }
        )
    except Exception as exc:
        registration_logger.warning(
            "Visitor registration proxy: unexpected registration flow failure",
            extra={
                "target": redact_url_token(visitor_info_url),
                "attempt": "registration_flow",
                "exception_class": exc.__class__.__name__,
            },
        )
        return JsonResponse({"detail": "registration failed"}, status=502)


@csrf_exempt
def register_visitor_telemetry(request):
    """Record client-side registration telemetry with redacted values."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)
    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    stage = str(payload.get("stage") or "unspecified").strip()
    message = str(payload.get("message") or "").strip()
    target = str(payload.get("target") or "").strip()
    token = str(payload.get("token") or "").strip()

    target_host = ""
    target_port: int | None = None
    try:
        parsed_target = urlsplit(target)
        target_host = parsed_target.hostname or ""
        target_port = parsed_target.port or (
            443 if parsed_target.scheme == "https" else 80
        )
    except Exception:
        pass

    route_ip = ""
    if target_host:
        route_ip = _get_route_address(target_host, target_port or 0)

    extra_fields = {
        k: v
        for k, v in payload.items()
        if k not in {"stage", "message", "target", "token"}
    }
    if target_host and "target_host" not in extra_fields:
        extra_fields["target_host"] = target_host
    if target_port and "target_port" not in extra_fields:
        extra_fields["target_port"] = target_port
    if route_ip and "route_ip" not in extra_fields:
        extra_fields["route_ip"] = route_ip

    registration_logger.info(
        "Visitor registration telemetry stage=%s target=%s token_redacted=%s client_ip=%s host_ip=%s user_agent=%s message=%s extra=%s",
        stage,
        redact_url_token(target),
        redact_token_value(token),
        get_client_ip(request) or "",
        route_ip or _get_host_ip(request) or "",
        request.headers.get("User-Agent", ""),
        message,
        json.dumps(extra_fields, default=str),
    )
    return JsonResponse({"status": "ok"})
