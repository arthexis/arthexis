import base64
import ipaddress
import json
import re
import secrets
import socket
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
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from pathlib import Path
from urllib.parse import urlsplit

from utils.api import api_login_required

from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

from core.models import RFID

from .rfid_sync import apply_rfid_payload, serialize_rfid

from .models import Node, NetMessage, PendingNetMessage, node_information_updated
from .utils import capture_screenshot, save_screenshot


PROXY_TOKEN_SALT = "nodes.proxy.session"
PROXY_TOKEN_TIMEOUT = 300
PROXY_CACHE_PREFIX = "nodes:proxy-session:"


def _load_signed_node(request, requester_id: str):
    signature = request.headers.get("X-Signature")
    if not signature:
        return None, JsonResponse({"detail": "signature required"}, status=403)
    node = Node.objects.filter(uuid=requester_id).first()
    if not node or not node.public_key:
        return None, JsonResponse({"detail": "unknown requester"}, status=403)
    try:
        public_key = serialization.load_pem_public_key(node.public_key.encode())
        public_key.verify(
            base64.b64decode(signature),
            request.body,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception:
        return None, JsonResponse({"detail": "invalid signature"}, status=403)
    return node, None


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
        if desired_role and node.role_id != desired_role.id:
            node.role = desired_role
            update_fields.append("role")
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

    node, error_response = _load_signed_node(request, requester)
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

    node, error_response = _load_signed_node(request, requester)
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
