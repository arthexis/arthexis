import base64
import ipaddress
import json
import re
import socket
import uuid
from datetime import timedelta
from collections.abc import Mapping
from django.apps import apps
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core import serializers
from django.core.cache import cache
from django.http import JsonResponse
from django.http.request import split_domain_port
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.cache import patch_vary_headers
from django.views.decorators.csrf import csrf_exempt

from utils.api import api_login_required

from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

from django.db import IntegrityError, transaction
from django.db.models import Q

from apps.cards.models import RFID
from apps.ocpp import store
from apps.ocpp.models import Charger
from apps.ocpp.network import (
    apply_remote_charger_payload,
    serialize_charger_for_network,
    sync_transactions_payload,
)
from apps.ocpp.transactions_io import export_transactions
from asgiref.sync import async_to_sync


from .models import (
    Node,
    NetMessage,
    PendingNetMessage,
    NodeRole,
    node_information_updated,
)
from .utils import capture_screenshot, save_screenshot




def _load_signed_node(
    request,
    requester_id: str,
    *,
    mac_address: str | None = None,
    public_key: str | None = None,
):
    signature = request.headers.get("X-Signature")
    if not signature:
        return None, JsonResponse({"detail": "signature required"}, status=403)
    try:
        signature_bytes = base64.b64decode(signature)
    except Exception:
        return None, JsonResponse({"detail": "invalid signature"}, status=403)

    candidates: list[Node] = []
    seen: set[int] = set()

    lookup_values: list[tuple[str, str]] = []
    if requester_id:
        lookup_values.append(("uuid", requester_id))
    if mac_address:
        lookup_values.append(("mac_address__iexact", mac_address))
    if public_key:
        lookup_values.append(("public_key", public_key))

    for field, value in lookup_values:
        node = Node.objects.filter(**{field: value}).first()
        if not node or not node.public_key:
            continue
        if node.pk is not None and node.pk in seen:
            continue
        if node.pk is not None:
            seen.add(node.pk)
        candidates.append(node)

    if not candidates:
        return None, JsonResponse({"detail": "unknown requester"}, status=403)

    for node in candidates:
        try:
            loaded_key = serialization.load_pem_public_key(node.public_key.encode())
            loaded_key.verify(
                signature_bytes,
                request.body,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
        except Exception:
            continue
        return node, None

    return None, JsonResponse({"detail": "invalid signature"}, status=403)


def _clean_requester_hint(value, *, strip: bool = True) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip() if strip else value
    if not cleaned:
        return None
    return cleaned


def _normalize_requested_chargers(values) -> list[tuple[str, int | None, object]]:
    if not isinstance(values, list):
        return []

    normalized: list[tuple[str, int | None, object]] = []
    for entry in values:
        if not isinstance(entry, Mapping):
            continue
        serial = Charger.normalize_serial(entry.get("charger_id"))
        if not serial or Charger.is_placeholder_serial(serial):
            continue
        connector = entry.get("connector_id")
        if connector in ("", None):
            connector_value = None
        elif isinstance(connector, int):
            connector_value = connector
        else:
            try:
                connector_value = int(str(connector))
            except (TypeError, ValueError):
                connector_value = None
        since_raw = entry.get("since")
        since_dt = None
        if isinstance(since_raw, str):
            since_dt = parse_datetime(since_raw)
            if since_dt is not None and timezone.is_naive(since_dt):
                since_dt = timezone.make_aware(since_dt, timezone.get_current_timezone())
        normalized.append((serial, connector_value, since_dt))
    return normalized


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

    if request.is_secure():
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
            "last_seen": node.last_seen,
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
    host_domain = _get_host_domain(request)
    advertised_address = _get_advertised_address(request, node)
    preferred_port = node.get_preferred_port()
    advertised_port = node.port or preferred_port
    if host_domain:
        host_port = _get_host_port(request)
        if host_port in {preferred_port, node.port, 80, 443}:
            advertised_port = host_port
        else:
            advertised_port = preferred_port
    if host_domain:
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
        hostname = node.hostname
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
    }

    if token:
        try:
            priv_path = node.get_base_path() / "security" / f"{node.public_endpoint}"
            private_key = serialization.load_pem_private_key(
                priv_path.read_bytes(), password=None
            )
            signature = private_key.sign(
                token.encode(),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
            data["token_signature"] = base64.b64encode(signature).decode()
        except Exception:
            pass

    response = JsonResponse(data)
    response["Access-Control-Allow-Origin"] = "*"
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


# CSRF exemption retained so gateway hardware posting signed JSON without
# browser cookies can register successfully.
@csrf_exempt
def register_node(request):
    """Register or update a node from POSTed JSON data."""

    if request.method == "OPTIONS":
        response = JsonResponse({"detail": "ok"})
        return _add_cors_headers(request, response)

    if request.method != "POST":
        response = JsonResponse({"detail": "POST required"}, status=400)
        return _add_cors_headers(request, response)

    try:
        data = json.loads(request.body.decode())
    except json.JSONDecodeError:
        data = request.POST

    authenticated_user = getattr(request, "user", None)
    if not getattr(authenticated_user, "is_authenticated", False):
        authenticated_user = _authenticate_basic_credentials(request)

    if hasattr(data, "getlist"):
        raw_features = data.getlist("features")
        if not raw_features:
            features = None
        elif len(raw_features) == 1:
            features = raw_features[0]
        else:
            features = raw_features
    else:
        features = data.get("features")

    hostname = (data.get("hostname") or "").strip()
    address = (data.get("address") or "").strip()
    network_hostname = (data.get("network_hostname") or "").strip()
    if hasattr(data, "getlist"):
        ipv4_values = data.getlist("ipv4_address")
        raw_ipv4 = ipv4_values if ipv4_values else data.get("ipv4_address")
    else:
        raw_ipv4 = data.get("ipv4_address")
    ipv4_candidates = Node.sanitize_ipv4_addresses(raw_ipv4)
    ipv6_address = (data.get("ipv6_address") or "").strip()
    port = data.get("port", 8888)
    mac_address = (data.get("mac_address") or "").strip()
    public_key = data.get("public_key")
    token = data.get("token")
    signature = data.get("signature")
    installed_version = data.get("installed_version")
    installed_revision = data.get("installed_revision")
    relation_present = False
    if hasattr(data, "getlist"):
        relation_present = "current_relation" in data
    else:
        relation_present = "current_relation" in data
    raw_relation = data.get("current_relation")
    relation_value = (
        Node.normalize_relation(raw_relation) if relation_present else None
    )

    deactivate_user = _coerce_bool(data.get("deactivate_user"))

    if not hostname or not mac_address:
        response = JsonResponse(
            {"detail": "hostname and mac_address required"}, status=400
        )
        return _add_cors_headers(request, response)

    if not any([
        address,
        network_hostname,
        bool(ipv4_candidates),
        ipv6_address,
    ]):
        response = JsonResponse(
            {
                "detail": "at least one of address, network_hostname, "
                "ipv4_address, or ipv6_address must be provided",
            },
            status=400,
        )
        return _add_cors_headers(request, response)

    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 8888

    verified = False
    if public_key and token and signature:
        try:
            pub = serialization.load_pem_public_key(public_key.encode())
            pub.verify(
                base64.b64decode(signature),
                token.encode(),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
            verified = True
        except Exception:
            response = JsonResponse({"detail": "invalid signature"}, status=403)
            return _add_cors_headers(request, response)

    if not verified and not request.user.is_authenticated:
        response = JsonResponse({"detail": "authentication required"}, status=401)
        return _add_cors_headers(request, response)

    if not verified and request.user.is_authenticated:
        required_perms = ("nodes.add_node", "nodes.change_node")
        if not request.user.has_perms(required_perms):
            response = JsonResponse({"detail": "permission denied"}, status=403)
            return _add_cors_headers(request, response)

    trusted_requested = data.get("trusted")
    trusted_allowed = bool(trusted_requested) and (
        verified or request.user.is_authenticated
    )

    mac_address = mac_address.lower()
    address_value = address or ""
    ipv6_value = ipv6_address or ""
    for candidate in Node.sanitize_ipv4_addresses([address, network_hostname, hostname]):
        if candidate not in ipv4_candidates:
            ipv4_candidates.append(candidate)
    ipv4_value = Node.serialize_ipv4_addresses(ipv4_candidates) or ""

    for candidate in (address, network_hostname, hostname):
        candidate = (candidate or "").strip()
        if not candidate:
            continue
        try:
            parsed_ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if parsed_ip.version == 6 and not ipv6_value:
            ipv6_value = str(parsed_ip)
    defaults = {
        "hostname": hostname,
        "network_hostname": network_hostname,
        "address": address_value,
        "ipv4_address": ipv4_value,
        "ipv6_address": ipv6_value,
        "port": port,
    }
    if trusted_allowed:
        defaults["trusted"] = True
    role_name = str(data.get("role") or data.get("role_name") or "").strip()
    desired_role = None
    if role_name and (verified or request.user.is_authenticated):
        desired_role = NodeRole.objects.filter(name=role_name).first()
        if desired_role:
            defaults["role"] = desired_role
    if verified:
        defaults["public_key"] = public_key
    if installed_version is not None:
        defaults["installed_version"] = str(installed_version)[:20]
    if installed_revision is not None:
        defaults["installed_revision"] = str(installed_revision)[:40]
    if relation_value is not None:
        defaults["current_relation"] = relation_value

    node, created = Node.objects.get_or_create(
        mac_address=mac_address,
        defaults=defaults,
    )
    if not created:
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
        timestamp = timezone.now()
        node.last_seen = timestamp
        if "last_seen" not in update_fields:
            update_fields.append("last_seen")

        if update_fields:
            # ``auto_now`` fields such as ``last_seen`` are not updated when
            # ``update_fields`` is provided unless they are explicitly
            # included. Ensure the heartbeat timestamp is always refreshed so
            # remote syncs reflect the latest contact time even when no other
            # fields changed.
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
        if features is not None and (verified or request.user.is_authenticated):
            if isinstance(features, (str, bytes)):
                feature_list = [features]
            else:
                feature_list = list(features)
            node.update_manual_features(feature_list)
        response = JsonResponse(
            {
                "id": node.id,
                "uuid": str(node.uuid),
                "detail": f"Node already exists (id: {node.id})",
            }
        )
        if deactivate_user:
            deactivate = getattr(request.user, "deactivate_temporary_credentials", None)
            if callable(deactivate):
                deactivate()
        return _add_cors_headers(request, response)

    if features is not None and (verified or request.user.is_authenticated):
        if isinstance(features, (str, bytes)):
            feature_list = [features]
        else:
            feature_list = list(features)
        node.update_manual_features(feature_list)

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

    _announce_visitor_join(node, relation_value)

    response = JsonResponse({"id": node.id, "uuid": str(node.uuid)})
    if deactivate_user:
        deactivate = getattr(request.user, "deactivate_temporary_credentials", None)
        if callable(deactivate):
            deactivate()
    return _add_cors_headers(request, response)


@api_login_required
def capture(request):
    """Capture a screenshot of the site's root URL and record it."""

    url = request.build_absolute_uri("/")
    try:
        path = capture_screenshot(url)
    except Exception as exc:  # pragma: no cover - depends on selenium setup
        return JsonResponse({"detail": str(exc)}, status=500)
    node = Node.get_local()
    screenshot = save_screenshot(path, node=node, method=request.method)
    node_id = screenshot.node.id if screenshot and screenshot.node else None
    return JsonResponse({"screenshot": str(path), "node": node_id})


@csrf_exempt
def network_chargers(request):
    """Return serialized charger information for trusted peers."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)

    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    requester = body.get("requester")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)

    requester_mac = _clean_requester_hint(body.get("requester_mac"))
    requester_public_key = _clean_requester_hint(
        body.get("requester_public_key"), strip=False
    )

    node, error_response = _load_signed_node(
        request,
        requester,
        mac_address=requester_mac,
        public_key=requester_public_key,
    )
    if error_response is not None:
        return error_response

    requested = _normalize_requested_chargers(body.get("chargers") or [])

    qs = Charger.objects.all()
    local_node = Node.get_local()
    if local_node:
        qs = qs.filter(Q(node_origin=local_node) | Q(node_origin__isnull=True))

    if requested:
        filters = Q()
        for serial, connector_value, _ in requested:
            if connector_value is None:
                filters |= Q(charger_id=serial, connector_id__isnull=True)
            else:
                filters |= Q(charger_id=serial, connector_id=connector_value)
        qs = qs.filter(filters)

    chargers = [serialize_charger_for_network(charger) for charger in qs]

    include_transactions = bool(body.get("include_transactions"))
    response_data: dict[str, object] = {"chargers": chargers}

    if include_transactions:
        serials = [serial for serial, _, _ in requested] or list(
            {charger["charger_id"] for charger in chargers}
        )
        since_values = [since for _, _, since in requested if since]
        start = min(since_values) if since_values else None
        tx_payload = export_transactions(start=start, chargers=serials or None)
        response_data["transactions"] = tx_payload

    return JsonResponse(response_data)


@csrf_exempt
def forward_chargers(request):
    """Receive forwarded charger metadata and transactions from trusted peers."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)

    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    requester = body.get("requester")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)

    requester_mac = _clean_requester_hint(body.get("requester_mac"))
    requester_public_key = _clean_requester_hint(
        body.get("requester_public_key"), strip=False
    )

    node, error_response = _load_signed_node(
        request,
        requester,
        mac_address=requester_mac,
        public_key=requester_public_key,
    )
    if error_response is not None:
        return error_response

    processed = 0
    chargers_payload = body.get("chargers", [])
    if not isinstance(chargers_payload, list):
        chargers_payload = []
    for entry in chargers_payload:
        if not isinstance(entry, Mapping):
            continue
        charger = apply_remote_charger_payload(node, entry)
        if charger:
            processed += 1

    imported = 0
    transactions_payload = body.get("transactions")
    if isinstance(transactions_payload, Mapping):
        imported = sync_transactions_payload(transactions_payload)

    return JsonResponse({"status": "ok", "chargers": processed, "transactions": imported})


def _require_local_origin(charger: Charger) -> bool:
    local = Node.get_local()
    if not local:
        return charger.node_origin_id is None
    if charger.node_origin_id is None:
        return True
    return charger.node_origin_id == local.pk


def _send_trigger_status(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    payload: dict[str, object] = {"requestedMessage": "StatusNotification"}
    if connector_value is not None:
        payload["connectorId"] = connector_value
    message_id = uuid.uuid4().hex
    msg = json.dumps([2, message_id, "TriggerMessage", payload])
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send TriggerMessage ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "TriggerMessage",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "trigger_target": "StatusNotification",
            "trigger_connector": connector_value,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        timeout=5.0,
        action="TriggerMessage",
        log_key=log_key,
        message="TriggerMessage StatusNotification timed out",
    )
    return True, "requested status update", {}


def _send_get_configuration(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    message_id = uuid.uuid4().hex
    msg = json.dumps([2, message_id, "GetConfiguration", {}])
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send GetConfiguration ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "GetConfiguration",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        timeout=5.0,
        action="GetConfiguration",
        log_key=log_key,
        message=(
            "GetConfiguration timed out: charger did not respond"
            " (operation may not be supported)"
        ),
    )
    return True, "requested configuration update", {}


def _send_reset(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    tx = store.get_transaction(charger.charger_id, connector_value)
    if tx:
        return False, "active session in progress", {}
    message_id = uuid.uuid4().hex
    reset_type = None
    if payload:
        reset_type = payload.get("reset_type")
    msg = json.dumps(
        [2, message_id, "Reset", {"type": (reset_type or "Soft")}]
    )
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send Reset ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "Reset",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        timeout=5.0,
        action="Reset",
        log_key=log_key,
        message="Reset timed out: charger did not respond",
    )
    return True, "reset requested", {}


def _toggle_rfid(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    enable = None
    if payload is not None:
        enable = payload.get("enable")
    if isinstance(enable, str):
        enable = enable.lower() in {"1", "true", "yes", "on"}
    elif isinstance(enable, (int, bool)):
        enable = bool(enable)
    if enable is None:
        enable = not charger.require_rfid
    enable_bool = bool(enable)
    Charger.objects.filter(pk=charger.pk).update(require_rfid=enable_bool)
    charger.require_rfid = enable_bool
    detail = "RFID authentication enabled" if enable_bool else "RFID authentication disabled"
    return True, detail, {"require_rfid": enable_bool}


def _send_local_rfid_list_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    authorization_list = []
    if payload is not None:
        authorization_list = payload.get("local_authorization_list", []) or []
    if not isinstance(authorization_list, list):
        return False, "local_authorization_list must be a list", {}
    list_version = None
    if payload is not None:
        list_version = payload.get("list_version")
    if list_version is None:
        list_version_value = (charger.local_auth_list_version or 0) + 1
    else:
        try:
            list_version_value = int(list_version)
        except (TypeError, ValueError):
            return False, "invalid list_version", {}
        if list_version_value <= 0:
            return False, "invalid list_version", {}
    update_type = "Full"
    if payload is not None and payload.get("update_type"):
        update_type = str(payload.get("update_type") or "").strip() or "Full"
    message_id = uuid.uuid4().hex
    msg_payload = {
        "listVersion": list_version_value,
        "updateType": update_type,
        "localAuthorizationList": authorization_list,
    }
    msg = json.dumps([2, message_id, "SendLocalList", msg_payload])
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send SendLocalList ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "SendLocalList",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "list_version": list_version_value,
            "list_size": len(authorization_list),
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        action="SendLocalList",
        log_key=log_key,
        message="SendLocalList request timed out",
    )
    return True, "SendLocalList dispatched", {}


def _get_local_list_version_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    message_id = uuid.uuid4().hex
    msg = json.dumps([2, message_id, "GetLocalListVersion", {}])
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send GetLocalListVersion ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "GetLocalListVersion",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        action="GetLocalListVersion",
        log_key=log_key,
        message="GetLocalListVersion request timed out",
    )
    return True, "GetLocalListVersion requested", {}


def _change_availability_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    availability_type = None
    if payload is not None:
        availability_type = payload.get("availability_type")
    availability_label = str(availability_type or "").strip()
    if availability_label not in {"Operative", "Inoperative"}:
        return False, "invalid availability type", {}
    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    connector_id = connector_value if connector_value is not None else 0
    message_id = uuid.uuid4().hex
    msg = json.dumps(
        [
            2,
            message_id,
            "ChangeAvailability",
            {"connectorId": connector_id, "type": availability_label},
        ]
    )
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send ChangeAvailability ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    timestamp = timezone.now()
    store.register_pending_call(
        message_id,
        {
            "action": "ChangeAvailability",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "availability_type": availability_label,
            "requested_at": timestamp,
        },
    )
    updates = {
        "availability_requested_state": availability_label,
        "availability_requested_at": timestamp,
        "availability_request_status": "",
        "availability_request_status_at": None,
        "availability_request_details": "",
    }
    Charger.objects.filter(pk=charger.pk).update(**updates)
    for field, value in updates.items():
        setattr(charger, field, value)
    return True, f"requested ChangeAvailability {availability_label}", updates


def _clear_cache_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    message_id = uuid.uuid4().hex
    msg = json.dumps([2, message_id, "ClearCache", {}])
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send ClearCache ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    requested_at = timezone.now()
    store.register_pending_call(
        message_id,
        {
            "action": "ClearCache",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action="ClearCache",
        log_key=log_key,
    )
    return True, "requested ClearCache", {}


def _clear_charging_profile_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = 0
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    message_id = uuid.uuid4().hex
    msg = json.dumps([2, message_id, "ClearChargingProfile", {}])
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send ClearChargingProfile ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    requested_at = timezone.now()
    store.register_pending_call(
        message_id,
        {
            "action": "ClearChargingProfile",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action="ClearChargingProfile",
        log_key=log_key,
    )
    return True, "requested ClearChargingProfile", {}


def _unlock_connector_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    if connector_value in (None, 0):
        return False, "connector id is required", {}
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    message_id = uuid.uuid4().hex
    msg = json.dumps(
        [2, message_id, "UnlockConnector", {"connectorId": connector_value}]
    )
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send UnlockConnector ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    requested_at = timezone.now()
    store.register_pending_call(
        message_id,
        {
            "action": "UnlockConnector",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action="UnlockConnector",
        log_key=log_key,
        message="UnlockConnector request timed out",
    )
    return True, "requested UnlockConnector", {}


def _set_availability_state_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    availability_state = None
    if payload is not None:
        availability_state = payload.get("availability_state")
    availability_label = str(availability_state or "").strip()
    if availability_label not in {"Operative", "Inoperative"}:
        return False, "invalid availability state", {}
    timestamp = timezone.now()
    updates = {
        "availability_state": availability_label,
        "availability_state_updated_at": timestamp,
    }
    Charger.objects.filter(pk=charger.pk).update(**updates)
    for field, value in updates.items():
        setattr(charger, field, value)
    return True, f"availability marked {availability_label}", updates


def _remote_stop_transaction_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    tx_obj = store.get_transaction(charger.charger_id, connector_value)
    if tx_obj is None:
        return False, "no active transaction", {}
    message_id = uuid.uuid4().hex
    msg = json.dumps(
        [
            2,
            message_id,
            "RemoteStopTransaction",
            {"transactionId": tx_obj.pk},
        ]
    )
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send RemoteStopTransaction ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "RemoteStopTransaction",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "transaction_id": tx_obj.pk,
            "log_key": log_key,
            "requested_at": timezone.now(),
        },
    )
    return True, "remote stop requested", {}


def _prepare_diagnostics_upload_payload(
    request, charger: Charger, payload: Mapping | None
) -> dict[str, object]:
    """Ensure a Media Bucket-backed diagnostics location is present."""

    prepared: dict[str, object] = {}
    if isinstance(payload, Mapping):
        prepared = dict(payload)

    location = str(prepared.get("location") or "").strip()
    if location:
        prepared["location"] = location
        return prepared

    expires_at = timezone.now() + timedelta(days=30)
    bucket = charger.ensure_diagnostics_bucket(expires_at=expires_at)
    upload_path = reverse("ocpp:media-bucket-upload", kwargs={"slug": bucket.slug})
    location = request.build_absolute_uri(upload_path)
    prepared["location"] = location
    if bucket.expires_at:
        prepared.setdefault("stopTime", bucket.expires_at.isoformat())

    Charger.objects.filter(pk=charger.pk).update(
        diagnostics_bucket=bucket, diagnostics_location=location
    )
    charger.diagnostics_bucket = bucket
    charger.diagnostics_location = location
    return prepared


def _request_diagnostics_remote(
    charger: Charger, payload: Mapping | None = None, *, request=None
) -> tuple[bool, str, dict[str, object]]:
    if request is not None:
        payload = _prepare_diagnostics_upload_payload(request, charger, payload)

    location = ""
    stop_time_raw = None
    if isinstance(payload, Mapping):
        location = str(payload.get("location") or "").strip()
        stop_time_raw = payload.get("stopTime")
    if not location:
        return False, "missing upload location", {}

    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}

    stop_time_value = _parse_remote_datetime(stop_time_raw)
    message_id = uuid.uuid4().hex
    request_payload: dict[str, object] = {"location": location}
    if stop_time_value:
        request_payload["stopTime"] = stop_time_value.isoformat()
    msg = json.dumps([2, message_id, "GetDiagnostics", request_payload])
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send GetDiagnostics ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "GetDiagnostics",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "location": location,
            "requested_at": timezone.now(),
        },
    )
    return True, "diagnostics requested", {}


REMOTE_ACTIONS = {
    "trigger-status": _send_trigger_status,
    "get-configuration": _send_get_configuration,
    "reset": _send_reset,
    "toggle-rfid": _toggle_rfid,
    "send-local-rfid-list": _send_local_rfid_list_remote,
    "get-local-list-version": _get_local_list_version_remote,
    "change-availability": _change_availability_remote,
    "clear-cache": _clear_cache_remote,
    "clear-charging-profile": _clear_charging_profile_remote,
    "unlock-connector": _unlock_connector_remote,
    "set-availability-state": _set_availability_state_remote,
    "remote-stop": _remote_stop_transaction_remote,
    "request-diagnostics": _request_diagnostics_remote,
}


@csrf_exempt
def network_charger_action(request):
    """Execute remote admin actions on behalf of trusted nodes."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)

    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    requester = body.get("requester")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)

    requester_mac = _clean_requester_hint(body.get("requester_mac"))
    requester_public_key = _clean_requester_hint(
        body.get("requester_public_key"), strip=False
    )

    node, error_response = _load_signed_node(
        request,
        requester,
        mac_address=requester_mac,
        public_key=requester_public_key,
    )
    if error_response is not None:
        return error_response

    serial = Charger.normalize_serial(body.get("charger_id"))
    if not serial or Charger.is_placeholder_serial(serial):
        return JsonResponse({"detail": "invalid charger"}, status=400)

    connector = body.get("connector_id")
    if connector in ("", None):
        connector_value = None
    elif isinstance(connector, int):
        connector_value = connector
    else:
        try:
            connector_value = int(str(connector))
        except (TypeError, ValueError):
            return JsonResponse({"detail": "invalid connector"}, status=400)

    charger = Charger.objects.filter(
        charger_id=serial, connector_id=connector_value
    ).first()
    if not charger:
        return JsonResponse({"detail": "charger not found"}, status=404)

    if not charger.allow_remote:
        return JsonResponse({"detail": "remote actions disabled"}, status=403)

    if not _require_local_origin(charger):
        return JsonResponse({"detail": "charger is not managed by this node"}, status=403)

    authorized_node_ids = {
        pk for pk in (charger.manager_node_id, charger.node_origin_id) if pk
    }
    if authorized_node_ids and node and node.pk not in authorized_node_ids:
        return JsonResponse(
            {"detail": "requester does not manage this charger"}, status=403
        )

    action = body.get("action")
    handler = REMOTE_ACTIONS.get(action or "")
    if handler is None:
        return JsonResponse({"detail": "unsupported action"}, status=400)

    if action == "request-diagnostics":
        success, message, updates = handler(charger, body, request=request)
    else:
        success, message, updates = handler(charger, body)

    status_code = 200 if success else 409
    status_label = "ok" if success else "error"
    serialized_updates: dict[str, object] = {}
    if isinstance(updates, Mapping):
        for key, value in updates.items():
            if hasattr(value, "isoformat"):
                serialized_updates[key] = value.isoformat()
            else:
                serialized_updates[key] = value
    return JsonResponse(
        {"status": status_label, "detail": message, "updates": serialized_updates},
        status=status_code,
    )



@csrf_exempt
def net_message(request):
    """Receive a network message and continue propagation."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=400)
    try:
        data = json.loads(request.body.decode())
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    signature = request.headers.get("X-Signature")
    sender_id = data.get("sender")
    if not signature or not sender_id:
        return JsonResponse({"detail": "signature required"}, status=403)
    node = Node.objects.filter(uuid=sender_id).first()
    if not node or not node.public_key:
        return JsonResponse({"detail": "unknown sender"}, status=403)
    try:
        public_key = serialization.load_pem_public_key(node.public_key.encode())
        public_key.verify(
            base64.b64decode(signature),
            request.body,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
    except Exception:
        return JsonResponse({"detail": "invalid signature"}, status=403)

    try:
        msg = NetMessage.receive_payload(data, sender=node)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    return JsonResponse({"status": "propagated", "complete": msg.complete})


@csrf_exempt
def net_message_pull(request):
    """Allow downstream nodes to retrieve queued network messages."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    requester = data.get("requester")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)
    signature = request.headers.get("X-Signature")
    if not signature:
        return JsonResponse({"detail": "signature required"}, status=403)

    node = Node.objects.filter(uuid=requester).first()
    if not node or not node.public_key:
        return JsonResponse({"detail": "unknown requester"}, status=403)
    try:
        public_key = serialization.load_pem_public_key(node.public_key.encode())
        public_key.verify(
            base64.b64decode(signature),
            request.body,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
    except Exception:
        return JsonResponse({"detail": "invalid signature"}, status=403)

    local = Node.get_local()
    if not local:
        return JsonResponse({"detail": "local node unavailable"}, status=503)
    private_key = local.get_private_key()
    if not private_key:
        return JsonResponse({"detail": "signing unavailable"}, status=503)

    entries = (
        PendingNetMessage.objects.select_related(
            "message",
            "message__filter_node",
            "message__filter_node_feature",
            "message__filter_node_role",
            "message__node_origin",
        )
        .filter(node=node)
        .order_by("queued_at")
    )
    messages: list[dict[str, object]] = []
    expired_ids: list[int] = []
    delivered_ids: list[int] = []

    origin_fallback = str(local.uuid)

    for entry in entries:
        if entry.is_stale:
            expired_ids.append(entry.pk)
            continue
        message = entry.message
        reach_source = message.filter_node_role or message.reach
        reach_name = reach_source.name if reach_source else None
        origin_node = message.node_origin
        origin_uuid = str(origin_node.uuid) if origin_node else origin_fallback
        sender_id = str(local.uuid)
        seen = [str(value) for value in entry.seen]
        payload = message._build_payload(
            sender_id=sender_id,
            origin_uuid=origin_uuid,
            reach_name=reach_name,
            seen=seen,
        )
        payload_json = message._serialize_payload(payload)
        payload_signature = message._sign_payload(payload_json, private_key)
        if not payload_signature:
            logger.warning(
                "Unable to sign queued NetMessage %s for node %s", message.pk, node.pk
            )
            continue
        messages.append({"payload": payload, "signature": payload_signature})
        delivered_ids.append(entry.pk)

    if expired_ids:
        PendingNetMessage.objects.filter(pk__in=expired_ids).delete()
    if delivered_ids:
        PendingNetMessage.objects.filter(pk__in=delivered_ids).delete()

    return JsonResponse({"messages": messages})
