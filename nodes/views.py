import base64
import ipaddress
import json
import socket
from collections.abc import Mapping

from django.http import JsonResponse
from django.http.request import split_domain_port
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404
from django.conf import settings
from django.urls import reverse
from pathlib import Path
from django.utils.cache import patch_vary_headers

from utils.api import api_login_required

from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

from core.models import RFID

from .rfid_sync import apply_rfid_payload, serialize_rfid

from .models import (
    Node,
    NetMessage,
    NodeFeature,
    NodeRole,
    node_information_updated,
)
from .utils import capture_screenshot, save_screenshot


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
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return domain
    return ""


def _get_advertised_address(request, node) -> str:
    """Return the best address for the client to reach this node."""

    client_ip = _get_client_ip(request)
    route_address = _get_route_address(client_ip, node.port)
    if route_address:
        return route_address
    host_ip = _get_host_ip(request)
    if host_ip:
        return host_ip
    return node.address


@api_login_required
def node_list(request):
    """Return a JSON list of all known nodes."""

    nodes = [
        {
            "hostname": node.hostname,
            "address": node.address,
            "port": node.port,
            "last_seen": node.last_seen,
            "features": list(node.features.values_list("slug", flat=True)),
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
    if host_domain:
        hostname = host_domain
        if advertised_address and advertised_address != node.address:
            address = advertised_address
        else:
            address = host_domain
    else:
        hostname = node.hostname
        address = advertised_address
    data = {
        "hostname": hostname,
        "address": address,
        "port": node.port,
        "mac_address": node.mac_address,
        "public_key": node.public_key,
        "features": list(node.features.values_list("slug", flat=True)),
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

    for attr in ("hostname", "public_endpoint", "address"):
        value = getattr(node, attr, "") or ""
        value = value.strip()
        if value:
            return value
    identifier = getattr(node, "pk", None)
    return str(identifier or node)


def _announce_visitor_join(new_node: Node, relation: Node.Relation | None) -> None:
    """Emit a network message when the visitor node links to a host."""

    if relation != Node.Relation.UPSTREAM:
        return

    local_node = Node.get_local()
    if not local_node:
        return

    visitor_name = _node_display_name(local_node)
    host_name = _node_display_name(new_node)
    NetMessage.broadcast(subject=f"NODE {visitor_name}", body=f"JOINS {host_name}")


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

    hostname = data.get("hostname")
    address = data.get("address")
    port = data.get("port", 8000)
    mac_address = data.get("mac_address")
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

    if not hostname or not address or not mac_address:
        response = JsonResponse(
            {"detail": "hostname, address and mac_address required"}, status=400
        )
        return _add_cors_headers(request, response)

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
    defaults = {
        "hostname": hostname,
        "address": address,
        "port": port,
    }
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
        node.hostname = hostname
        node.address = address
        node.port = port
        update_fields = ["hostname", "address", "port"]
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
            {"id": node.id, "detail": f"Node already exists (id: {node.id})"}
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

    response = JsonResponse({"id": node.id})
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
    signature = request.headers.get("X-Signature")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)
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
    signature = request.headers.get("X-Signature")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)
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
            "address": node.address,
            "port": node.port,
            "badge_color": node.badge_color,
            "last_seen": node.last_seen,
            "features": list(node.features.values_list("slug", flat=True)),
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

    msg_uuid = data.get("uuid")
    subject = data.get("subject", "")
    body = data.get("body", "")
    attachments = NetMessage.normalize_attachments(data.get("attachments"))
    reach_name = data.get("reach")
    reach_role = None
    if reach_name:
        reach_role = NodeRole.objects.filter(name=reach_name).first()
    filter_node_uuid = data.get("filter_node")
    filter_node = None
    if filter_node_uuid:
        filter_node = Node.objects.filter(uuid=filter_node_uuid).first()
    filter_feature_slug = data.get("filter_node_feature")
    filter_feature = None
    if filter_feature_slug:
        filter_feature = NodeFeature.objects.filter(slug=filter_feature_slug).first()
    filter_role_name = data.get("filter_node_role")
    filter_role = None
    if filter_role_name:
        filter_role = NodeRole.objects.filter(name=filter_role_name).first()
    filter_relation_value = data.get("filter_current_relation")
    filter_relation = ""
    if filter_relation_value:
        relation = Node.normalize_relation(filter_relation_value)
        filter_relation = relation.value if relation else ""
    filter_installed_version = (data.get("filter_installed_version") or "")[:20]
    filter_installed_revision = (data.get("filter_installed_revision") or "")[:40]
    seen = data.get("seen", [])
    origin_id = data.get("origin")
    origin_node = None
    if origin_id:
        origin_node = Node.objects.filter(uuid=origin_id).first()
    if not origin_node:
        origin_node = node
    if not msg_uuid:
        return JsonResponse({"detail": "uuid required"}, status=400)
    msg, created = NetMessage.objects.get_or_create(
        uuid=msg_uuid,
        defaults={
            "subject": subject[:64],
            "body": body[:256],
            "reach": reach_role,
            "node_origin": origin_node,
            "attachments": attachments or None,
            "filter_node": filter_node,
            "filter_node_feature": filter_feature,
            "filter_node_role": filter_role,
            "filter_current_relation": filter_relation,
            "filter_installed_version": filter_installed_version,
            "filter_installed_revision": filter_installed_revision,
        },
    )
    if not created:
        msg.subject = subject[:64]
        msg.body = body[:256]
        update_fields = ["subject", "body"]
        if reach_role and msg.reach_id != reach_role.id:
            msg.reach = reach_role
            update_fields.append("reach")
        if msg.node_origin_id is None and origin_node:
            msg.node_origin = origin_node
            update_fields.append("node_origin")
        if attachments and msg.attachments != attachments:
            msg.attachments = attachments
            update_fields.append("attachments")
        field_updates = {
            "filter_node": filter_node,
            "filter_node_feature": filter_feature,
            "filter_node_role": filter_role,
            "filter_current_relation": filter_relation,
            "filter_installed_version": filter_installed_version,
            "filter_installed_revision": filter_installed_revision,
        }
        for field, value in field_updates.items():
            if getattr(msg, field) != value:
                setattr(msg, field, value)
                update_fields.append(field)
        msg.save(update_fields=update_fields)
    if attachments:
        msg.apply_attachments(attachments)
    msg.propagate(seen=seen)
    return JsonResponse({"status": "propagated", "complete": msg.complete})


def last_net_message(request):
    """Return the most recent :class:`NetMessage`."""

    msg = NetMessage.objects.order_by("-created").first()
    if not msg:
        return JsonResponse({"subject": "", "body": "", "admin_url": ""})
    return JsonResponse(
        {
            "subject": msg.subject,
            "body": msg.body,
            "admin_url": reverse("admin:nodes_netmessage_change", args=[msg.pk]),
        }
    )
