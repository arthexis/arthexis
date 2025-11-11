import base64
import ipaddress
import ipaddress
import json
import re
import secrets
import socket
import uuid
from collections.abc import Mapping
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login
from django.contrib.auth.models import Group, Permission
from django.core import serializers
from django.core.cache import cache
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.http import HttpResponse, JsonResponse
from django.http.request import split_domain_port
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from pathlib import Path
from urllib.parse import urlsplit

from utils.api import api_login_required

from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

from django.db import IntegrityError, transaction
from django.db.models import Q

from core.models import RFID
from ocpp import store
from ocpp.models import Charger
from ocpp.network import (
    apply_remote_charger_payload,
    serialize_charger_for_network,
    sync_transactions_payload,
)
from ocpp.transactions_io import export_transactions
from asgiref.sync import async_to_sync

from .rfid_sync import apply_rfid_payload, serialize_rfid

from .models import (
    Node,
    NetMessage,
    PendingNetMessage,
    NodeRole,
    node_information_updated,
)
from .utils import capture_screenshot, save_screenshot


PROXY_TOKEN_SALT = "nodes.proxy.session"
PROXY_TOKEN_TIMEOUT = 300
PROXY_CACHE_PREFIX = "nodes:proxy-session:"


_CONSTELLATION_SUBNET_VALUE = getattr(settings, "CONSTELLATION_WG_SUBNET", "10.88.0.0/24")
try:
    CONSTELLATION_SUBNET = ipaddress.ip_network(
        _CONSTELLATION_SUBNET_VALUE, strict=False
    )
except ValueError:  # pragma: no cover - defensive fallback for misconfiguration
    CONSTELLATION_SUBNET = ipaddress.ip_network("10.88.0.0/24")

CONSTELLATION_INTERFACE = getattr(
    settings, "CONSTELLATION_WG_INTERFACE", "wg-constellation"
)
try:
    CONSTELLATION_PORT = int(getattr(settings, "CONSTELLATION_WG_PORT", 51820))
except (TypeError, ValueError):  # pragma: no cover - defensive fallback
    CONSTELLATION_PORT = 51820
CONSTELLATION_ENDPOINT = getattr(settings, "CONSTELLATION_WG_ENDPOINT", "")
CONSTELLATION_STATE_DIR = Path(
    getattr(
        settings,
        "CONSTELLATION_WG_STATE_DIR",
        Path(settings.BASE_DIR) / "security" / "wireguard",
    )
)
CONSTELLATION_PUBLIC_KEY_PATH = CONSTELLATION_STATE_DIR / f"{CONSTELLATION_INTERFACE}.pub"
CONSTELLATION_PEERS_PATH = CONSTELLATION_STATE_DIR / "peers.json"
CONSTELLATION_LOCK_PATH = Path(settings.BASE_DIR) / "locks" / "constellation_ip.lck"
CONSTELLATION_DEVICE_PATTERN = re.compile(r"^gw[0-9]{1,6}$")


def _normalize_constellation_ip(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


def _ensure_constellation_device(node: Node) -> str | None:
    """Assign and return a unique Constellation device label for ``node``."""

    current = (node.constellation_device or "").strip()
    if current and CONSTELLATION_DEVICE_PATTERN.fullmatch(current):
        return current

    attempt = 1
    while True:
        candidate = f"gw{attempt}"
        if (
            Node.objects.filter(constellation_device=candidate)
            .exclude(pk=node.pk)
            .exists()
        ):
            attempt += 1
            continue
        try:
            with transaction.atomic():
                try:
                    node.refresh_from_db(fields=["constellation_device"], using=None)
                except node.__class__.DoesNotExist:
                    return None
                current = (node.constellation_device or "").strip()
                if current and CONSTELLATION_DEVICE_PATTERN.fullmatch(current):
                    return current
                node.constellation_device = candidate
                node.save(update_fields=["constellation_device"])
                return candidate
        except IntegrityError:
            attempt += 1
            continue


def _load_constellation_peers() -> dict[str, dict]:
    if not CONSTELLATION_PEERS_PATH.exists():
        return {}
    try:
        raw = json.loads(CONSTELLATION_PEERS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(raw, dict):
        peers = raw.get("peers")
        if isinstance(peers, dict):
            return peers
        return raw
    return {}


def _save_constellation_peers(peers: dict[str, dict]) -> None:
    try:
        CONSTELLATION_STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = CONSTELLATION_PEERS_PATH.with_suffix(".tmp")
        tmp_path.write_text(json.dumps({"peers": peers}, indent=2, sort_keys=True))
        tmp_path.replace(CONSTELLATION_PEERS_PATH)
    except OSError:
        pass


def _write_constellation_lock(ip_value: str) -> None:
    if not ip_value:
        return
    try:
        CONSTELLATION_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONSTELLATION_LOCK_PATH.write_text(f"{ip_value}\n")
    except OSError:
        pass


def _resolve_constellation_endpoint(request, local_node: Node) -> tuple[str, int]:
    host = ""
    port = CONSTELLATION_PORT
    explicit = (CONSTELLATION_ENDPOINT or "").strip()
    if explicit:
        explicit_host, explicit_port = split_domain_port(explicit)
        host = explicit_host or explicit
        if explicit_port:
            try:
                port = int(explicit_port)
            except ValueError:
                port = CONSTELLATION_PORT
    if not host:
        domain = _get_host_domain(request)
        if domain:
            host = domain
    if not host and getattr(local_node, "network_hostname", ""):
        host = local_node.network_hostname
    if not host and getattr(local_node, "hostname", ""):
        host = local_node.hostname
    if not host:
        host = local_node.get_primary_contact() or host
    if not host:
        try:
            raw_host = request.get_host()
        except Exception:  # pragma: no cover - defensive
            raw_host = ""
        if raw_host:
            fallback_host, fallback_port = split_domain_port(raw_host)
            if fallback_host:
                host = fallback_host
            if fallback_port:
                try:
                    port = int(fallback_port)
                except ValueError:
                    port = CONSTELLATION_PORT
    if port <= 0 or port > 65535:
        port = CONSTELLATION_PORT
    return host, port


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
                padding.PKCS1v15(),
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


def _sanitize_proxy_target(target: str | None, request) -> str:
    default_target = reverse("admin:index")
    if not target:
        return default_target
    candidate = str(target).strip()
    if not candidate:
        return default_target
    if candidate.startswith(("http://", "https://")):
        parsed = urlsplit(candidate)
        if not parsed.path:
            return default_target
        allowed = url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
        if not allowed:
            return default_target
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path
    if not candidate.startswith("/"):
        candidate = f"/{candidate}"
    return candidate


def _assign_groups_and_permissions(user, payload: Mapping) -> None:
    groups = payload.get("groups", [])
    group_objs: list[Group] = []
    if isinstance(groups, (list, tuple)):
        for name in groups:
            if not isinstance(name, str):
                continue
            cleaned = name.strip()
            if not cleaned:
                continue
            group, _ = Group.objects.get_or_create(name=cleaned)
            group_objs.append(group)
    if group_objs or user.groups.exists():
        user.groups.set(group_objs)

    permissions = payload.get("permissions", [])
    perm_objs: list[Permission] = []
    if isinstance(permissions, (list, tuple)):
        for label in permissions:
            if not isinstance(label, str):
                continue
            app_label, _, codename = label.partition(".")
            if not app_label or not codename:
                continue
            perm = Permission.objects.filter(
                content_type__app_label=app_label, codename=codename
            ).first()
            if perm:
                perm_objs.append(perm)
    if perm_objs:
        user.user_permissions.set(perm_objs)


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
            "constellation_ip": node.constellation_ip,
            "constellation_device": node.constellation_device,
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
def constellation_setup(request):
    """Assign and return WireGuard configuration details for Constellation peers."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    mac_address = (payload.get("mac_address") or "").strip().lower()
    if not mac_address:
        return JsonResponse({"detail": "mac_address required"}, status=400)

    public_key = (payload.get("public_key") or "").strip()
    if not public_key:
        return JsonResponse({"detail": "public_key required"}, status=400)

    endpoint_hint = (payload.get("endpoint") or "").strip()
    hostname_hint = (payload.get("hostname") or "").strip()

    node = Node.objects.filter(mac_address__iexact=mac_address).first()
    if node is None:
        return JsonResponse({"detail": "unknown node"}, status=404)

    local_node = Node.get_local()
    if local_node is None:
        local_node, _ = Node.register_current()
    if local_node is None:
        return JsonResponse({"detail": "constellation hub unavailable"}, status=503)

    role_name = (local_node.role.name if local_node.role_id else "").lower()
    if role_name != "watchtower":
        return JsonResponse({"detail": "constellation hub not configured"}, status=503)

    hub_ip = _normalize_constellation_ip(local_node.constellation_ip)
    if not hub_ip:
        host_iter = CONSTELLATION_SUBNET.hosts()
        try:
            hub_ip_candidate = str(next(host_iter))
        except StopIteration:  # pragma: no cover - subnet misconfiguration
            return JsonResponse({"detail": "constellation subnet has no hosts"}, status=503)
        hub_ip = hub_ip_candidate
        local_node.constellation_ip = hub_ip
        local_node.save(update_fields=["constellation_ip"])
        _write_constellation_lock(hub_ip)

    assigned_ip = _normalize_constellation_ip(node.constellation_ip)
    if assigned_ip:
        try:
            if ipaddress.ip_address(assigned_ip) not in CONSTELLATION_SUBNET:
                assigned_ip = ""
        except ValueError:
            assigned_ip = ""
        if assigned_ip == hub_ip:
            assigned_ip = ""

    used_ips: set[str] = set()
    for value in (
        Node.objects.exclude(pk=node.pk)
        .exclude(constellation_ip__isnull=True)
        .values_list("constellation_ip", flat=True)
    ):
        normalized = _normalize_constellation_ip(value)
        if normalized:
            used_ips.add(normalized)
    used_ips.add(hub_ip)

    if node.pk == local_node.pk:
        assigned_ip = hub_ip

    if not assigned_ip:
        for host in CONSTELLATION_SUBNET.hosts():
            candidate = str(host)
            if candidate == hub_ip or candidate in used_ips:
                continue
            assigned_ip = candidate
            break

    if not assigned_ip:
        return JsonResponse({"detail": "no available constellation addresses"}, status=503)

    if _normalize_constellation_ip(node.constellation_ip) != assigned_ip:
        node.constellation_ip = assigned_ip
        node.save(update_fields=["constellation_ip"])

    hub_device = _ensure_constellation_device(local_node)
    peer_device = _ensure_constellation_device(node)

    peers_state = _load_constellation_peers()
    peer_entry = {
        "public_key": public_key,
        "ip": assigned_ip,
        "allowed_ips": [f"{assigned_ip}/32"],
        "hostname": hostname_hint or node.hostname or mac_address,
        "node_id": node.pk,
        "last_seen": timezone.now().isoformat(),
    }
    if peer_device:
        peer_entry["constellation_device"] = peer_device
    if endpoint_hint:
        peer_entry["endpoint"] = endpoint_hint
    peers_state[mac_address] = peer_entry
    _save_constellation_peers(peers_state)

    try:
        hub_public_key = CONSTELLATION_PUBLIC_KEY_PATH.read_text().strip()
    except OSError:
        hub_public_key = ""
    if not hub_public_key:
        return JsonResponse({"detail": "constellation hub key unavailable"}, status=503)

    endpoint_host, endpoint_port = _resolve_constellation_endpoint(request, local_node)
    hub_allowed = [str(CONSTELLATION_SUBNET)]
    hub_data = {
        "public_key": hub_public_key,
        "endpoint_host": endpoint_host,
        "endpoint_port": endpoint_port,
        "allowed_ips": hub_allowed,
        "constellation_ip": hub_ip,
        "persistent_keepalive": 25,
    }

    peer_configs: list[dict[str, object]] = []
    peer_devices: dict[str, str] = {}
    mac_map = [key.lower() for key in peers_state.keys()]
    if mac_map:
        filters = Q()
        for mac in mac_map:
            filters |= Q(mac_address__iexact=mac)
        if filters:
            for record in Node.objects.filter(filters):
                if record.mac_address:
                    peer_devices[record.mac_address.lower()] = (
                        record.constellation_device or ""
                    )
    if node.pk == local_node.pk:
        for peer_mac, entry in peers_state.items():
            if peer_mac == mac_address:
                continue
            peer_key = (entry.get("public_key") or "").strip()
            peer_ip = _normalize_constellation_ip(entry.get("ip", ""))
            if not peer_key or not peer_ip:
                continue
            allowed_ips = entry.get("allowed_ips") or [f"{peer_ip}/32"]
            device_name = (
                peer_devices.get(peer_mac.lower())
                or (entry.get("constellation_device") or "")
            )
            peer_configs.append(
                {
                    "mac_address": peer_mac,
                    "public_key": peer_key,
                    "constellation_ip": peer_ip,
                    "allowed_ips": allowed_ips,
                    "endpoint": entry.get("endpoint", ""),
                    "node_id": entry.get("node_id"),
                    "last_seen": entry.get("last_seen"),
                    "constellation_device": device_name,
                }
            )

    response = {
        "interface": CONSTELLATION_INTERFACE,
        "assigned_ip": assigned_ip,
        "address": f"{assigned_ip}/{CONSTELLATION_SUBNET.prefixlen}",
        "subnet": str(CONSTELLATION_SUBNET),
        "hub": {**hub_data, "constellation_device": hub_device or ""},
        "dns": [hub_ip],
        "peers": peer_configs,
        "constellation_device": peer_device or "",
    }

    return JsonResponse(response)


@csrf_exempt
def node_info(request):
    """Return information about the local node and sign ``token`` if provided."""

    node = Node.get_local()
    if node is None:
        node, _ = Node.register_current()

    token = request.GET.get("token", "")
    host_domain = _get_host_domain(request)
    advertised_address = _get_advertised_address(request, node)
    advertised_port = node.port
    if host_domain:
        host_port = _get_host_port(request)
        if host_port:
            advertised_port = host_port
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
        "constellation_ip": node.constellation_ip,
        "constellation_device": node.constellation_device,
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
            priv_path = (
                Path(node.base_path or settings.BASE_DIR)
                / "security"
                / f"{node.public_endpoint}"
            )
            private_key = serialization.load_pem_private_key(
                priv_path.read_bytes(), password=None
            )
            signature = private_key.sign(
                token.encode(),
                padding.PKCS1v15(),
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
    constellation_ip = (data.get("constellation_ip") or "").strip()
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
        constellation_ip,
    ]):
        response = JsonResponse(
            {
                "detail": "at least one of address, network_hostname, "
                "ipv4_address, ipv6_address or constellation_ip must be provided",
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
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            verified = True
        except Exception:
            response = JsonResponse({"detail": "invalid signature"}, status=403)
            return _add_cors_headers(request, response)

    if not verified and not request.user.is_authenticated:
        response = JsonResponse({"detail": "authentication required"}, status=401)
        return _add_cors_headers(request, response)

    mac_address = mac_address.lower()
    address_value = address or None
    ipv6_value = ipv6_address or None
    constellation_value = constellation_ip or None

    for candidate in Node.sanitize_ipv4_addresses([address, network_hostname, hostname]):
        if candidate not in ipv4_candidates:
            ipv4_candidates.append(candidate)
    ipv4_value = Node.serialize_ipv4_addresses(ipv4_candidates)

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
    if constellation_value:
        try:
            constellation_value = str(ipaddress.ip_address(constellation_value))
        except ValueError:
            constellation_value = None

    defaults = {
        "hostname": hostname,
        "network_hostname": network_hostname,
        "address": address_value,
        "ipv4_address": ipv4_value,
        "ipv6_address": ipv6_value,
        "port": port,
    }
    defaults["constellation_ip"] = constellation_value
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
        update_fields = []
        for field, value in (
            ("hostname", hostname),
            ("network_hostname", network_hostname),
            ("address", address_value),
            ("ipv4_address", ipv4_value),
            ("ipv6_address", ipv6_value),
            ("constellation_ip", constellation_value),
            ("port", port),
        ):
            if getattr(node, field) != value:
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
        if update_fields:
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
def export_rfids(request):
    """Return serialized RFID records for authenticated peers."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    requester = payload.get("requester")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)

    requester_mac = _clean_requester_hint(payload.get("requester_mac"))
    requester_public_key = _clean_requester_hint(
        payload.get("requester_public_key"), strip=False
    )
    node, error_response = _load_signed_node(
        request,
        requester,
        mac_address=requester_mac,
        public_key=requester_public_key,
    )
    if error_response is not None:
        return error_response

    tags = [serialize_rfid(tag) for tag in RFID.objects.all().order_by("label_id")]

    return JsonResponse({"rfids": tags})


@csrf_exempt
def import_rfids(request):
    """Import RFID payloads from a trusted peer."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    requester = payload.get("requester")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)

    requester_mac = _clean_requester_hint(payload.get("requester_mac"))
    requester_public_key = _clean_requester_hint(
        payload.get("requester_public_key"), strip=False
    )
    node, error_response = _load_signed_node(
        request,
        requester,
        mac_address=requester_mac,
        public_key=requester_public_key,
    )
    if error_response is not None:
        return error_response

    rfids = payload.get("rfids", [])
    if not isinstance(rfids, list):
        return JsonResponse({"detail": "rfids must be a list"}, status=400)

    created = 0
    updated = 0
    linked_accounts = 0
    missing_accounts: list[str] = []
    errors = 0

    for entry in rfids:
        if not isinstance(entry, Mapping):
            errors += 1
            continue
        outcome = apply_rfid_payload(entry, origin_node=node)
        if not outcome.ok:
            errors += 1
            if outcome.error:
                missing_accounts.append(outcome.error)
            continue
        if outcome.created:
            created += 1
        else:
            updated += 1
        linked_accounts += outcome.accounts_linked
        missing_accounts.extend(outcome.missing_accounts)

    return JsonResponse(
        {
            "processed": len(rfids),
            "created": created,
            "updated": updated,
            "accounts_linked": linked_accounts,
            "missing_accounts": missing_accounts,
            "errors": errors,
        }
    )


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


REMOTE_ACTIONS = {
    "trigger-status": _send_trigger_status,
    "get-configuration": _send_get_configuration,
    "reset": _send_reset,
    "toggle-rfid": _toggle_rfid,
    "send-local-rfid-list": _send_local_rfid_list_remote,
    "get-local-list-version": _get_local_list_version_remote,
    "change-availability": _change_availability_remote,
    "clear-cache": _clear_cache_remote,
    "set-availability-state": _set_availability_state_remote,
    "remote-stop": _remote_stop_transaction_remote,
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
def proxy_session(request):
    """Create a proxy login session for a remote administrator."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    requester = payload.get("requester")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)

    requester_mac = _clean_requester_hint(payload.get("requester_mac"))
    requester_public_key = _clean_requester_hint(
        payload.get("requester_public_key"), strip=False
    )
    node, error_response = _load_signed_node(
        request,
        requester,
        mac_address=requester_mac,
        public_key=requester_public_key,
    )
    if error_response is not None:
        return error_response

    user_payload = payload.get("user") or {}
    username = str(user_payload.get("username", "")).strip()
    if not username:
        return JsonResponse({"detail": "username required"}, status=400)

    User = get_user_model()
    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            "email": user_payload.get("email", ""),
            "first_name": user_payload.get("first_name", ""),
            "last_name": user_payload.get("last_name", ""),
        },
    )

    updates: list[str] = []
    for field in ("first_name", "last_name", "email"):
        value = user_payload.get(field)
        if isinstance(value, str) and getattr(user, field) != value:
            setattr(user, field, value)
            updates.append(field)

    if created:
        user.set_unusable_password()
        updates.append("password")

    staff_flag = user_payload.get("is_staff")
    if staff_flag is not None:
        is_staff = bool(staff_flag)
    else:
        is_staff = True
    if user.is_staff != is_staff:
        user.is_staff = is_staff
        updates.append("is_staff")

    superuser_flag = user_payload.get("is_superuser")
    if superuser_flag is not None:
        is_superuser = bool(superuser_flag)
        if user.is_superuser != is_superuser:
            user.is_superuser = is_superuser
            updates.append("is_superuser")

    if not user.is_active:
        user.is_active = True
        updates.append("is_active")

    if updates:
        user.save(update_fields=updates)

    _assign_groups_and_permissions(user, user_payload)

    target_path = _sanitize_proxy_target(payload.get("target"), request)
    nonce = secrets.token_urlsafe(24)
    cache_key = f"{PROXY_CACHE_PREFIX}{nonce}"
    cache.set(cache_key, {"user_id": user.pk}, PROXY_TOKEN_TIMEOUT)

    signer = TimestampSigner(salt=PROXY_TOKEN_SALT)
    token = signer.sign_object({"user": user.pk, "next": target_path, "nonce": nonce})
    login_url = request.build_absolute_uri(
        reverse("node-proxy-login", args=[token])
    )
    expires = timezone.now() + timedelta(seconds=PROXY_TOKEN_TIMEOUT)

    return JsonResponse({"login_url": login_url, "expires": expires.isoformat()})


@csrf_exempt
def proxy_login(request, token):
    """Redeem a proxy login token and redirect to the target path."""

    signer = TimestampSigner(salt=PROXY_TOKEN_SALT)
    try:
        payload = signer.unsign_object(token, max_age=PROXY_TOKEN_TIMEOUT)
    except SignatureExpired:
        return HttpResponse(status=410)
    except BadSignature:
        return HttpResponse(status=400)

    nonce = payload.get("nonce")
    if not nonce:
        return HttpResponse(status=400)

    cache_key = f"{PROXY_CACHE_PREFIX}{nonce}"
    cache_payload = cache.get(cache_key)
    if not cache_payload:
        return HttpResponse(status=410)
    cache.delete(cache_key)

    user_id = cache_payload.get("user_id")
    if not user_id:
        return HttpResponse(status=403)

    User = get_user_model()
    user = User.objects.filter(pk=user_id).first()
    if not user or not user.is_active:
        return HttpResponse(status=403)

    backend = getattr(user, "backend", "")
    if not backend:
        backends = getattr(settings, "AUTHENTICATION_BACKENDS", None) or ()
        backend = backends[0] if backends else "django.contrib.auth.backends.ModelBackend"
    login(request, user, backend=backend)

    next_path = payload.get("next") or reverse("admin:index")
    if not url_has_allowed_host_and_scheme(
        next_path,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_path = reverse("admin:index")

    return redirect(next_path)


def _suite_model_name(meta) -> str:
    base = str(meta.verbose_name_plural or meta.verbose_name or meta.object_name)
    normalized = re.sub(r"[^0-9A-Za-z]+", " ", base).title().replace(" ", "")
    return normalized or meta.object_name


@csrf_exempt
def proxy_execute(request):
    """Execute model operations on behalf of a remote interface node."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    requester = payload.get("requester")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)

    requester_mac = _clean_requester_hint(payload.get("requester_mac"))
    requester_public_key = _clean_requester_hint(
        payload.get("requester_public_key"), strip=False
    )
    node, error_response = _load_signed_node(
        request,
        requester,
        mac_address=requester_mac,
        public_key=requester_public_key,
    )
    if error_response is not None:
        return error_response

    action = str(payload.get("action", "")).strip().lower()
    if not action:
        return JsonResponse({"detail": "action required"}, status=400)

    credentials = payload.get("credentials") or {}
    username = str(credentials.get("username", "")).strip()
    password_value = credentials.get("password")
    password = password_value if isinstance(password_value, str) else str(password_value or "")
    if not username or not password:
        return JsonResponse({"detail": "credentials required"}, status=401)

    User = get_user_model()
    existing_user = User.objects.filter(username=username).first()
    auth_user = authenticate(request=None, username=username, password=password)

    if auth_user is None:
        if existing_user is not None:
            return JsonResponse({"detail": "authentication failed"}, status=403)
        auth_user = User.objects.create_user(
            username=username,
            password=password,
            email=str(credentials.get("email", "")),
        )
        auth_user.is_staff = True
        auth_user.is_superuser = True
        auth_user.first_name = str(credentials.get("first_name", ""))
        auth_user.last_name = str(credentials.get("last_name", ""))
        auth_user.save()
    else:
        updates: list[str] = []
        for field in ("first_name", "last_name", "email"):
            value = credentials.get(field)
            if isinstance(value, str) and getattr(auth_user, field) != value:
                setattr(auth_user, field, value)
                updates.append(field)
        for flag in ("is_staff", "is_superuser"):
            if flag in credentials:
                desired = bool(credentials.get(flag))
                if getattr(auth_user, flag) != desired:
                    setattr(auth_user, flag, desired)
                    updates.append(flag)
        if updates:
            auth_user.save(update_fields=updates)

    if not auth_user.is_active:
        return JsonResponse({"detail": "user inactive"}, status=403)

    _assign_groups_and_permissions(auth_user, credentials)

    model_label = payload.get("model")
    model = None
    if action != "schema":
        if not isinstance(model_label, str) or "." not in model_label:
            return JsonResponse({"detail": "model required"}, status=400)
        app_label, model_name = model_label.split(".", 1)
        model = apps.get_model(app_label, model_name)
        if model is None:
            return JsonResponse({"detail": "model not found"}, status=404)

    if action == "schema":
        models_payload = []
        for registered_model in apps.get_models():
            meta = registered_model._meta
            models_payload.append(
                {
                    "app_label": meta.app_label,
                    "model": meta.model_name,
                    "object_name": meta.object_name,
                    "verbose_name": str(meta.verbose_name),
                    "verbose_name_plural": str(meta.verbose_name_plural),
                    "suite_name": _suite_model_name(meta),
                }
            )
        return JsonResponse({"models": models_payload})

    action_perm = {
        "list": "view",
        "get": "view",
        "create": "add",
        "update": "change",
        "delete": "delete",
    }.get(action)

    if action_perm and not auth_user.is_superuser:
        perm_codename = f"{model._meta.app_label}.{action_perm}_{model._meta.model_name}"
        if not auth_user.has_perm(perm_codename):
            return JsonResponse({"detail": "forbidden"}, status=403)

    try:
        if action == "list":
            filters = payload.get("filters") or {}
            if filters and not isinstance(filters, Mapping):
                return JsonResponse({"detail": "filters must be a mapping"}, status=400)
            queryset = model._default_manager.all()
            if filters:
                queryset = queryset.filter(**filters)
            limit = payload.get("limit")
            if limit is not None:
                try:
                    limit_value = int(limit)
                    if limit_value > 0:
                        queryset = queryset[:limit_value]
                except (TypeError, ValueError):
                    pass
            data = serializers.serialize("python", queryset)
            return JsonResponse({"objects": data})

        if action == "get":
            filters = payload.get("filters") or {}
            if filters and not isinstance(filters, Mapping):
                return JsonResponse({"detail": "filters must be a mapping"}, status=400)
            lookup = dict(filters)
            if not lookup and "pk" in payload:
                lookup = {"pk": payload.get("pk")}
            if not lookup:
                return JsonResponse({"detail": "lookup required"}, status=400)
            obj = model._default_manager.get(**lookup)
            data = serializers.serialize("python", [obj])[0]
            return JsonResponse({"object": data})
    except model.DoesNotExist:
        return JsonResponse({"detail": "not found"}, status=404)
    except Exception as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    return JsonResponse({"detail": "unsupported action"}, status=400)


@csrf_exempt
@api_login_required
def public_node_endpoint(request, endpoint):
    """Public API endpoint for a node.

    - ``GET`` returns information about the node.
    - ``POST`` broadcasts the request body as a :class:`NetMessage`.
    """

    node = get_object_or_404(Node, public_endpoint=endpoint, enable_public_api=True)

    if request.method == "GET":
        data = {
            "hostname": node.hostname,
            "network_hostname": node.network_hostname,
            "address": node.address or node.get_primary_contact(),
            "ipv4_address": node.ipv4_address,
            "ipv6_address": node.ipv6_address,
            "port": node.port,
            "badge_color": node.badge_color,
            "last_seen": node.last_seen,
            "features": list(node.features.values_list("slug", flat=True)),
            "installed_version": node.installed_version,
            "installed_revision": node.installed_revision,
        }
        return JsonResponse(data)

    if request.method == "POST":
        NetMessage.broadcast(
            subject=request.method,
            body=request.body.decode("utf-8") if request.body else "",
            seen=[str(node.uuid)],
        )
        return JsonResponse({"status": "stored"})

    return JsonResponse({"detail": "Method not allowed"}, status=405)


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
            padding.PKCS1v15(),
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
            padding.PKCS1v15(),
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
