import base64
import ipaddress
import json
import logging
import socket
from importlib import import_module
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate
from django.contrib.sites.models import Site
from django.http import JsonResponse
from django.http.request import split_domain_port
from django.test.client import RequestFactory
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from config.request_utils import is_https_request
from utils.api import api_login_required

from apps.nodes.logging import get_register_visitor_logger

from ..models import Node, NodeRole, node_information_updated

logger = logging.getLogger("apps.nodes.views")
registration_logger = get_register_visitor_logger()


def _get_client_ip(request):
    """Return the client IP from the request headers."""

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        for value in forwarded_for.split(","):
            candidate = value.strip()
            if candidate:
                return candidate
    return request.META.get("REMOTE_ADDR", "")


def _get_route_address(remote_ip: str, port: int) -> str:
    """Return the local address used to reach ``remote_ip``."""

    if not remote_ip:
        return ""
    try:
        parsed = ipaddress.ip_address(remote_ip)
    except ValueError:
        return ""

    try:
        target_port = int(port)
    except (TypeError, ValueError):
        target_port = 1
    if target_port <= 0 or target_port > 65535:
        target_port = 1

    family = socket.AF_INET6 if parsed.version == 6 else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            if family == socket.AF_INET6:
                sock.connect((remote_ip, target_port, 0, 0))
            else:
                sock.connect((remote_ip, target_port))
            return sock.getsockname()[0]
    except OSError:
        return ""


def _get_host_ip(request) -> str:
    """Return the IP address from the host header if available."""

    try:
        host = request.get_host()
    except Exception:  # pragma: no cover - defensive
        return ""
    if not host:
        return ""
    domain, _ = split_domain_port(host)
    if not domain:
        return ""
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return ""
    return domain


def _get_host_domain(request) -> str:
    """Return the domain from the host header when it isn't an IP."""

    try:
        host = request.get_host()
    except Exception:  # pragma: no cover - defensive
        return ""
    if not host:
        return ""
    domain, _ = split_domain_port(host)
    if not domain:
        return ""
    if domain.lower() == "localhost":
        return ""
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return domain
    return ""


def _normalize_port(value: str | int | None) -> int | None:
    """Return ``value`` as an integer port number when valid."""

    if value in (None, ""):
        return None
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    if port <= 0 or port > 65535:
        return None
    return port


def _iter_port_fallback_urls(base_url: str):
    """Yield the provided URL and any additional port-based fallbacks."""

    yield base_url

    try:
        parsed = urlsplit(base_url)
    except Exception:
        return

    if not parsed.hostname:
        return

    if parsed.port != 8888:
        return

    netloc = parsed.hostname
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"

    for candidate_port in (8000,):
        if candidate_port == parsed.port:
            continue
        yield urlunsplit(
            (
                parsed.scheme or "https",
                f"{netloc}:{candidate_port}",
                parsed.path,
                parsed.query,
                parsed.fragment,
            )
        )


def _get_host_port(request) -> int | None:
    """Return the port implied by the current request if available."""

    forwarded_port = request.headers.get("X-Forwarded-Port") or request.META.get(
        "HTTP_X_FORWARDED_PORT"
    )
    port = _normalize_port(forwarded_port)
    if port:
        return port

    try:
        host = request.get_host()
    except Exception:  # pragma: no cover - defensive
        host = ""
    if host:
        _, host_port = split_domain_port(host)
        port = _normalize_port(host_port)
        if port:
            return port

    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    if forwarded_proto:
        scheme = forwarded_proto.split(",")[0].strip().lower()
        if scheme == "https":
            return 443
        if scheme == "http":
            return 80

    if is_https_request(request):
        return 443

    scheme = getattr(request, "scheme", "")
    if scheme.lower() == "https":
        return 443
    if scheme.lower() == "http":
        return 80

    return None


def _get_advertised_address(request, node) -> str:
    """Return the best address for the client to reach this node."""

    client_ip = _get_client_ip(request)
    route_address = _get_route_address(client_ip, node.port)
    if route_address:
        return route_address
    host_ip = _get_host_ip(request)
    if host_ip:
        return host_ip
    return node.get_primary_contact() or node.address or node.hostname


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
        }
        for node in Node.objects.prefetch_related("features")
    ]
    return JsonResponse({"nodes": nodes})


@csrf_exempt
def node_info(request):
    """Return information about the local node and sign ``token`` if provided."""

    node = Node.get_local()
    if node is None:
        node, _ = Node.register_current()

    token = request.GET.get("token", "")
    registration_logger.info(
        "Visitor registration: node_info requested token=%s client_ip=%s host_ip=%s",
        "present" if token else "absent",
        _get_client_ip(request) or "",
        _get_host_ip(request) or "",
    )
    host_domain = _get_host_domain(request)
    advertised_address = _get_advertised_address(request, node)
    preferred_port = node.get_preferred_port()
    advertised_port = node.port or preferred_port
    base_domain = node.get_base_domain()
    base_site_requires_https = bool(getattr(node.base_site, "require_https", False))
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
        "base_site_domain": base_domain,
    }

    if token:
        try:
            priv_path = node.get_base_path() / "security" / f"{node.public_endpoint}"
            private_key = serialization.load_pem_private_key(
                priv_path.read_bytes(), password=None
            )
        except Exception as exc:
            registration_logger.warning(
                "Visitor registration: unable to load key for %s: %s",
                node.public_endpoint,
                exc,
            )
        else:
            signature, error = Node.sign_payload(token, private_key)
            if signature:
                data["token_signature"] = signature
                registration_logger.info(
                    "Visitor registration: token signed for node %s",
                    node.public_endpoint,
                )
            else:
                registration_logger.warning(
                    "Visitor registration: unable to sign token for %s: %s",
                    node.public_endpoint,
                    error,
                )

    response = JsonResponse(data)
    response["Access-Control-Allow-Origin"] = "*"
    registration_logger.info(
        "Visitor registration: node_info response hostname=%s address=%s port=%s role=%s",
        data.get("hostname") or "",
        data.get("address") or "",
        data.get("port") or "",
        data.get("role") or "",
    )
    return response


def _add_cors_headers(request, response):
    origin = request.headers.get("Origin")
    if origin:
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Credentials"] = "true"
        allow_headers = request.headers.get(
            "Access-Control-Request-Headers", "Content-Type"
        )
        response["Access-Control-Allow-Headers"] = allow_headers
        response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        patch_vary_headers(response, ["Origin"])
        return response

    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Headers"] = "Content-Type"
    response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return response


def _extract_response_detail(response) -> str:
    try:
        payload = json.loads(response.content.decode())
    except Exception:
        payload = None
    if isinstance(payload, Mapping) and payload.get("detail"):
        return str(payload["detail"])
    try:
        return response.content.decode(errors="ignore")
    except Exception:
        return ""


def _coerce_bool(value) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _authenticate_basic_credentials(request):
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Basic "):
        return None
    try:
        encoded = header.split(" ", 1)[1]
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return None
    user = authenticate(request=request, username=username, password=password)
    if user is not None:
        request.user = user
        request._cached_user = user
    return user


def _node_display_name(node: Node) -> str:
    """Return a human-friendly name for ``node`` suitable for messaging."""

    for attr in (
        "hostname",
        "network_hostname",
        "public_endpoint",
        "address",
        "ipv6_address",
        "ipv4_address",
    ):
        value = getattr(node, attr, "") or ""
        value = value.strip()
        if value:
            return value
    identifier = getattr(node, "pk", None)
    return str(identifier or node)


def _announce_visitor_join(new_node: Node, relation: Node.Relation | None) -> None:
    """Retained for compatibility; Net Message broadcasts are no longer emitted."""

    # Historical behavior broadcasted a Net Message whenever a visitor node
    # linked to an upstream host. This side effect has been removed to keep the
    # network chatter focused on actionable events, but the helper is preserved
    # so callers remain stable.
    return None


@dataclass
class NodeRegistrationPayload:
    hostname: str
    mac_address: str
    address: str
    network_hostname: str
    ipv4_candidates: list[str]
    ipv6_address: str
    port: int
    features: object
    public_key: str | None
    token: str | None
    signature: str | None
    installed_version: object | None
    installed_revision: object | None
    relation_value: Node.Relation | None
    trusted_requested: object
    role_name: str
    deactivate_user: bool
    base_site_domain: str

    @classmethod
    def from_data(cls, data):
        features = _extract_features(data)
        hostname = (data.get("hostname") or "").strip()
        address = (data.get("address") or "").strip()
        network_hostname = (data.get("network_hostname") or "").strip()
        ipv4_candidates = _extract_ipv4_candidates(data)
        ipv6_address = (data.get("ipv6_address") or "").strip()
        port = _coerce_port(data.get("port", 8888))
        mac_address = (data.get("mac_address") or "").strip()
        public_key = data.get("public_key")
        token = data.get("token")
        signature = data.get("signature")
        installed_version = data.get("installed_version")
        installed_revision = data.get("installed_revision")
        raw_relation = data.get("current_relation")
        relation_present = (
            hasattr(data, "getlist") and "current_relation" in data
        ) or ("current_relation" in data)
        relation_value = (
            Node.normalize_relation(raw_relation) if relation_present else None
        )
        trusted_requested = data.get("trusted")
        role_name = str(data.get("role") or data.get("role_name") or "").strip()
        deactivate_user = _coerce_bool(data.get("deactivate_user"))
        base_site_domain = str(data.get("base_site_domain") or "").strip()

        return cls(
            hostname=hostname,
            mac_address=mac_address,
            address=address,
            network_hostname=network_hostname,
            ipv4_candidates=ipv4_candidates,
            ipv6_address=ipv6_address,
            port=port,
            features=features,
            public_key=public_key,
            token=token,
            signature=signature,
            installed_version=installed_version,
            installed_revision=installed_revision,
            relation_value=relation_value,
            trusted_requested=trusted_requested,
            role_name=role_name,
            deactivate_user=deactivate_user,
            base_site_domain=base_site_domain,
        )


def _extract_request_data(request):
    try:
        return json.loads(request.body.decode())
    except json.JSONDecodeError:
        return request.POST


def _extract_features(data):
    if hasattr(data, "getlist"):
        raw_features = data.getlist("features")
        if not raw_features:
            return None
        if len(raw_features) == 1:
            return raw_features[0]
        return raw_features
    return data.get("features")


def _extract_ipv4_candidates(data) -> list[str]:
    if hasattr(data, "getlist"):
        ipv4_values = data.getlist("ipv4_address")
        raw_ipv4 = ipv4_values if ipv4_values else data.get("ipv4_address")
    else:
        raw_ipv4 = data.get("ipv4_address")
    return Node.sanitize_ipv4_addresses(raw_ipv4)


def _coerce_port(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 8888


def _ensure_authenticated_user(request):
    authenticated_user = getattr(request, "user", None)
    if not getattr(authenticated_user, "is_authenticated", False):
        authenticated_user = _authenticate_basic_credentials(request)
    return authenticated_user


def _validate_payload(payload: NodeRegistrationPayload):
    if not payload.hostname or not payload.mac_address:
        return JsonResponse(
            {"detail": "hostname and mac_address required"}, status=400
        )
    if not any(
        [
            payload.address,
            payload.network_hostname,
            bool(payload.ipv4_candidates),
            payload.ipv6_address,
        ]
    ):
        return JsonResponse(
            {
                "detail": "at least one of address, network_hostname, "
                "ipv4_address, or ipv6_address must be provided",
            },
            status=400,
        )
    return None


def _verify_signature(payload: NodeRegistrationPayload):
    if not (payload.public_key and payload.token and payload.signature):
        return False, None
    try:
        pub = serialization.load_pem_public_key(payload.public_key.encode())
        pub.verify(
            base64.b64decode(payload.signature),
            payload.token.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return True, None
    except Exception:
        return False, JsonResponse({"detail": "invalid signature"}, status=403)


def _enforce_authentication(request, *, verified: bool):
    if verified:
        return None
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "authentication required"}, status=401)
    required_perms = ("nodes.add_node", "nodes.change_node")
    if not request.user.has_perms(required_perms):
        return JsonResponse({"detail": "permission denied"}, status=403)
    return None


def _normalize_addresses(payload: NodeRegistrationPayload):
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


def _build_registration_payload(info: Mapping[str, Any] | None, relation: str | None):
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

    role_value = ""
    if info:
        for candidate in (info.get("role"), info.get("role_name")):
            if isinstance(candidate, str) and candidate.strip():
                role_value = candidate.strip()
                break
    if role_value:
        payload["role"] = role_value

    return payload


def _apply_token_signature(payload: dict, info: Mapping[str, Any] | None, token: str):
    if not (info and token):
        return
    signature = info.get("token_signature")
    if signature:
        payload["token"] = token
        payload["signature"] = signature


def _append_token(url: str, token: str) -> str:
    if not (url and token):
        return url
    try:
        parsed = urlsplit(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["token"] = token
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query, doseq=True),
                parsed.fragment,
            )
        )
    except Exception:
        return url


def _resolve_role(role_name: str, *, can_assign: bool):
    if not (role_name and can_assign):
        return None
    return NodeRole.objects.filter(name=role_name).first()


def _update_features(node: Node, features, *, allow_update: bool):
    if features is None or not allow_update:
        return
    if isinstance(features, (str, bytes)):
        feature_list = [features]
    else:
        feature_list = list(features)
    node.update_manual_features(feature_list)


def _refresh_last_updated(node: Node, update_fields: list[str]):
    timestamp = timezone.now()
    node.last_updated = timestamp
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
    """Record a registration attempt and its outcome."""

    client_ip = _get_client_ip(request) or ""
    host_ip = _get_host_ip(request) or ""
    registration_logger.log(
        level,
        "Node registration %s: hostname=%s mac=%s relation=%s client_ip=%s host_ip=%s detail=%s",
        status,
        payload.hostname or "<unknown>",
        payload.mac_address or "<unknown>",
        payload.relation_value or "unspecified",
        client_ip,
        host_ip,
        detail or "",
    )


def _deactivate_user_if_requested(request, deactivate_user: bool):
    if not deactivate_user:
        return
    deactivate = getattr(request.user, "deactivate_temporary_credentials", None)
    if callable(deactivate):
        deactivate()


def _update_existing_node(
    node: Node,
    *,
    hostname: str,
    network_hostname: str,
    address_value: str,
    ipv4_value: str,
    ipv6_value: str,
    port: int,
    verified: bool,
    public_key: str | None,
    installed_version: object | None,
    installed_revision: object | None,
    relation_value: Node.Relation | None,
    desired_role: NodeRole | None,
    trusted_allowed: bool,
    base_site: Site | None,
    features,
    request,
    deactivate_user: bool,
):
    previous_version = (node.installed_version or "").strip()
    previous_revision = (node.installed_revision or "").strip()
    update_fields: list[str] = []
    for field, value in (
        ("hostname", hostname),
        ("network_hostname", network_hostname),
        ("address", address_value),
        ("ipv4_address", ipv4_value),
        ("ipv6_address", ipv6_value),
        ("port", port),
    ):
        current = getattr(node, field)
        if isinstance(value, str):
            value = value or ""
            current = current or ""
        if current != value:
            setattr(node, field, value)
            update_fields.append(field)
    if verified:
        node.public_key = public_key
        update_fields.append("public_key")
    if installed_version is not None:
        node.installed_version = str(installed_version)[:20]
        if "installed_version" not in update_fields:
            update_fields.append("installed_version")
    if installed_revision is not None:
        node.installed_revision = str(installed_revision)[:40]
        if "installed_revision" not in update_fields:
            update_fields.append("installed_revision")
    if relation_value is not None and node.current_relation != relation_value:
        node.current_relation = relation_value
        update_fields.append("current_relation")
    if desired_role and node.role_id != desired_role.id:
        node.role = desired_role
        update_fields.append("role")
    if trusted_allowed and not node.trusted:
        node.trusted = True
        update_fields.append("trusted")
    if base_site and node.base_site_id != base_site.id:
        node.base_site = base_site
        update_fields.append("base_site")

    _refresh_last_updated(node, update_fields)

    if update_fields:
        # ``auto_now`` fields such as ``last_updated`` are not updated when
        # ``update_fields`` is provided unless they are explicitly included.
        # Ensure the heartbeat timestamp is always refreshed so remote syncs
        # reflect the latest contact time even when no other fields changed.
        node.save(update_fields=update_fields)
    current_version = (node.installed_version or "").strip()
    current_revision = (node.installed_revision or "").strip()
    node_information_updated.send(
        sender=Node,
        node=node,
        previous_version=previous_version,
        previous_revision=previous_revision,
        current_version=current_version,
        current_revision=current_revision,
        request=request,
    )
    _update_features(
        node,
        features,
        allow_update=verified or request.user.is_authenticated,
    )
    response = JsonResponse(
        {
            "id": node.id,
            "uuid": str(node.uuid),
            "detail": f"Node already exists (id: {node.id})",
        }
    )
    _deactivate_user_if_requested(request, deactivate_user)
    return response


# CSRF exemption retained so gateway hardware posting signed JSON without
# browser cookies can register successfully.
@csrf_exempt
def register_node(request):
    """Register or update a node from POSTed JSON data."""

    registration_logger.info(
        "Visitor registration: register_node called method=%s path=%s client_ip=%s host_ip=%s",
        request.method,
        request.path,
        _get_client_ip(request) or "",
        _get_host_ip(request) or "",
    )

    if request.method == "OPTIONS":
        response = JsonResponse({"detail": "ok"})
        logger.info(
            "Node registration preflight: client_ip=%s host_ip=%s",
            _get_client_ip(request) or "",
            _get_host_ip(request) or "",
        )
        return _add_cors_headers(request, response)

    if request.method != "POST":
        response = JsonResponse({"detail": "POST required"}, status=400)
        logger.warning(
            "Node registration invalid method %s: client_ip=%s host_ip=%s",
            request.method,
            _get_client_ip(request) or "",
            _get_host_ip(request) or "",
        )
        return _add_cors_headers(request, response)

    data = _extract_request_data(request)
    _ensure_authenticated_user(request)
    payload = NodeRegistrationPayload.from_data(data)

    registration_logger.info(
        "Visitor registration: payload parsed hostname=%s mac=%s relation=%s trusted_requested=%s",
        payload.hostname or "",
        payload.mac_address or "",
        payload.relation_value or "",
        bool(payload.trusted_requested),
    )

    _log_registration_event("attempt", payload, request)

    validation_response = _validate_payload(payload)
    if validation_response:
        _log_registration_event(
            "failed",
            payload,
            request,
            detail=_extract_response_detail(validation_response),
            level=logging.WARNING,
        )
        return _add_cors_headers(request, validation_response)

    verified, signature_error = _verify_signature(payload)
    registration_logger.info(
        "Visitor registration: signature verification %s",
        "passed" if verified and not signature_error else "failed",
    )

    if signature_error and request.user.is_authenticated:
        registration_logger.warning(
            "Visitor registration: signature invalid but authenticated user present; proceeding as unsigned"
        )
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
        return _add_cors_headers(request, signature_error)

    auth_error = _enforce_authentication(request, verified=verified)
    if auth_error:
        _log_registration_event(
            "denied",
            payload,
            request,
            detail=_extract_response_detail(auth_error),
            level=logging.WARNING,
        )
        return _add_cors_headers(request, auth_error)

    mac_address, address_value, ipv6_value, ipv4_value = _normalize_addresses(
        payload
    )
    trusted_allowed = bool(payload.trusted_requested) and (
        verified or request.user.is_authenticated
    )
    can_assign_role = verified or request.user.is_authenticated
    desired_role = _resolve_role(payload.role_name, can_assign=can_assign_role)
    base_site = None
    if payload.base_site_domain:
        base_site = Site.objects.filter(domain__iexact=payload.base_site_domain).first()

    defaults = {
        "hostname": payload.hostname,
        "network_hostname": payload.network_hostname,
        "address": address_value,
        "ipv4_address": ipv4_value,
        "ipv6_address": ipv6_value,
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
    if payload.relation_value is not None:
        defaults["current_relation"] = payload.relation_value

    node, created = Node.objects.get_or_create(
        mac_address=mac_address,
        defaults=defaults,
    )
    registration_logger.info(
        "Visitor registration: node lookup mac=%s created=%s node_id=%s",
        mac_address,
        created,
        node.id,
    )
    if not created:
        response = _update_existing_node(
            node,
            hostname=payload.hostname,
            network_hostname=payload.network_hostname,
            address_value=address_value,
            ipv4_value=ipv4_value,
            ipv6_value=ipv6_value,
            port=payload.port,
            verified=verified,
            public_key=payload.public_key,
            installed_version=payload.installed_version,
            installed_revision=payload.installed_revision,
            relation_value=payload.relation_value,
            desired_role=desired_role,
            trusted_allowed=trusted_allowed,
            base_site=base_site,
            features=payload.features,
            request=request,
            deactivate_user=payload.deactivate_user,
        )
        _log_registration_event(
            "succeeded",
            payload,
            request,
            detail=f"updated node {node.id}",
        )
        return _add_cors_headers(request, response)

    _update_features(
        node,
        payload.features,
        allow_update=verified or request.user.is_authenticated,
    )

    current_version = (node.installed_version or "").strip()
    current_revision = (node.installed_revision or "").strip()
    node_information_updated.send(
        sender=Node,
        node=node,
        previous_version="",
        previous_revision="",
        current_version=current_version,
        current_revision=current_revision,
        request=request,
    )

    _announce_visitor_join(node, payload.relation_value)

    response = JsonResponse({"id": node.id, "uuid": str(node.uuid)})
    _log_registration_event(
        "succeeded",
        payload,
        request,
        detail=f"created node {node.id}",
    )
    _deactivate_user_if_requested(request, payload.deactivate_user)
    return _add_cors_headers(request, response)


@staff_member_required
@require_POST
def register_visitor_proxy(request):
    """Server-side visitor registration to avoid browser mixed-content issues."""

    try:
        data = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    visitor_info_url = str(data.get("visitor_info_url") or "").strip()
    visitor_register_url = str(data.get("visitor_register_url") or "").strip()
    token = str(data.get("token") or "").strip()

    if not visitor_info_url or not visitor_register_url:
        return JsonResponse({"detail": "visitor info/register URLs required"}, status=400)

    parsed_info = urlsplit(visitor_info_url)
    parsed_register = urlsplit(visitor_register_url)
    if parsed_info.scheme != "https" or parsed_register.scheme != "https":
        return JsonResponse({"detail": "HTTPS is required for visitor registration"}, status=400)

    visitor_info_url = _append_token(visitor_info_url, token)

    factory = RequestFactory()
    host_info_request = factory.get("/nodes/info/", {"token": token} if token else {})
    host_info_request.user = request.user
    host_info_request._cached_user = request.user
    host_info_response = node_info(host_info_request)
    try:
        host_info = json.loads(host_info_response.content.decode() or "{}")
    except json.JSONDecodeError:
        host_info = {}

    session = requests.Session()
    timeout_seconds = 45

    visitor_info = None
    last_error: Exception | None = None
    for candidate_info_url in _iter_port_fallback_urls(visitor_info_url):
        try:
            visitor_info_response = session.get(
                candidate_info_url, timeout=timeout_seconds
            )
            visitor_info_response.raise_for_status()
            visitor_info = visitor_info_response.json()
            visitor_info_url = candidate_info_url
            break
        except Exception as exc:
            last_error = exc

    if visitor_info is None:
        registration_logger.warning(
            "Visitor registration proxy: unable to fetch visitor info from %s: %s",
            visitor_info_url,
            last_error,
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
        host_register_body = json.loads(host_register_response.content.decode() or "{}")
    except json.JSONDecodeError:
        host_register_body = {}

    if host_register_response.status_code != 200 or not host_register_body.get("id"):
        detail = host_register_body.get("detail") or "host registration failed"
        return JsonResponse({"detail": detail}, status=host_register_response.status_code or 400)

    visitor_payload = _build_registration_payload(host_info, "Upstream")
    _apply_token_signature(visitor_payload, host_info, token)

    visitor_register_body = None
    last_error = None
    for candidate_register_url in _iter_port_fallback_urls(visitor_register_url):
        try:
            visitor_register_response = session.post(
                candidate_register_url,
                json=visitor_payload,
                timeout=timeout_seconds,
            )
            visitor_register_response.raise_for_status()
            visitor_register_body = visitor_register_response.json()
            visitor_register_url = candidate_register_url
            break
        except Exception as exc:
            last_error = exc

    if visitor_register_body is None:
        registration_logger.warning(
            "Visitor registration proxy: unable to notify visitor at %s: %s",
            visitor_register_url,
            last_error,
        )
        return JsonResponse({"detail": "visitor confirmation failed"}, status=502)

    return JsonResponse(
        {
            "host": {
                "detail": host_register_body.get("detail", ""),
                "id": host_register_body.get("id"),
            },
            "visitor": {
                "detail": visitor_register_body.get("detail", ""),
                "id": visitor_register_body.get("id"),
            },
        }
    )


@csrf_exempt
def register_visitor_telemetry(request):
    """Record client-side registration events for troubleshooting."""

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
        target_port = parsed_target.port
        if target_host and not target_port:
            target_port = 443 if parsed_target.scheme == "https" else 80
    except Exception:
        target_host = ""
        target_port = None

    route_ip = ""
    if target_host:
        views_module = import_module("apps.nodes.views")
        route_ip = views_module._get_route_address(target_host, target_port or 0)

    extra_fields = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "stage",
            "message",
            "target",
            "token",
        }
    }

    if target_host and "target_host" not in extra_fields:
        extra_fields["target_host"] = target_host
    if target_port and "target_port" not in extra_fields:
        extra_fields["target_port"] = target_port
    if route_ip and "route_ip" not in extra_fields:
        extra_fields["route_ip"] = route_ip

    registration_logger.info(
        "Visitor registration telemetry stage=%s target=%s token=%s client_ip=%s host_ip=%s user_agent=%s message=%s extra=%s",
        stage,
        target,
        token,
        _get_client_ip(request) or "",
        route_ip or _get_host_ip(request) or "",
        request.headers.get("User-Agent", ""),
        message,
        json.dumps(extra_fields, default=str),
    )

    return JsonResponse({"status": "ok"})
