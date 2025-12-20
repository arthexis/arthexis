import json
from collections.abc import Mapping

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.views import redirect_to_login
from django.contrib.admin.views.decorators import staff_member_required
from apps.nodes.models import Node, NodeFeature
from apps.sites.utils import landing
from apps.cards.sync import apply_rfid_payload, serialize_rfid
from apps.nodes.views import _clean_requester_hint, _load_signed_node

from .scanner import scan_sources, enable_deep_read_mode
from .reader import validate_rfid_value
from apps.cards.models import RFID
from .utils import build_mode_toggle
from apps.video.rfid import scan_camera_qr


def _request_wants_json(request):
    """Return True if the request expects a JSON response."""

    accept = request.headers.get("accept", "")
    if "application/json" in accept.lower():
        return True
    # Fallback for older callers that mark AJAX requests without Accept headers.
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _feature_enabled(slug: str) -> bool:
    """Return ``True`` when the feature identified by ``slug`` is active."""

    feature = NodeFeature.objects.filter(slug=slug).first()
    if not feature:
        return False
    try:
        return bool(feature.is_enabled)
    except Exception:
        return False


def scan_next(request):
    """Return the next scanned RFID tag or validate a client-provided value."""

    node = Node.get_local()
    role_name = node.role.name if node and node.role else ""
    allow_anonymous = role_name == "Control"
    rfid_feature_enabled = _feature_enabled("rfid-scanner")
    camera_feature_enabled = _feature_enabled("rpi-camera")
    prefer_camera = request.GET.get("source") == "camera"
    camera_only_mode = camera_feature_enabled and not rfid_feature_enabled

    if request.method != "POST" and not request.user.is_authenticated and not allow_anonymous:
        if _request_wants_json(request):
            return JsonResponse({"error": "Authentication required"}, status=401)
        return redirect_to_login(
            request.get_full_path(), reverse("pages:login")
        )
    if request.method == "POST":
        if not request.user.is_authenticated and not allow_anonymous:
            return JsonResponse({"error": "Authentication required"}, status=401)
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({"error": "Invalid JSON payload"}, status=400)
        rfid = payload.get("rfid") or payload.get("value")
        kind = payload.get("kind")
        endianness = payload.get("endianness")
        result = validate_rfid_value(rfid, kind=kind, endianness=endianness)
    else:
        endianness = request.GET.get("endianness")
        if prefer_camera or camera_only_mode:
            result = scan_camera_qr(endianness=endianness)
        else:
            result = scan_sources(request, endianness=endianness)
    status = 500 if result.get("error") else 200
    return JsonResponse(result, status=status)


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


@require_POST
@staff_member_required
def scan_deep(_request):
    """Enable deep read mode on the RFID scanner."""
    result = enable_deep_read_mode()
    status = 500 if result.get("error") else 200
    return JsonResponse(result, status=status)


@landing("Identity Validator")
def reader(request):
    """Public page to scan RFID tags."""
    node = Node.get_local()
    role_name = node.role.name if node and node.role else ""
    allow_anonymous = role_name == "Control"

    if not request.user.is_authenticated and not allow_anonymous:
        return redirect_to_login(
            request.get_full_path(), reverse("pages:login")
        )

    table_mode, toggle_url, toggle_label = build_mode_toggle(request)
    rfid_feature_enabled = _feature_enabled("rfid-scanner")
    camera_feature_enabled = _feature_enabled("rpi-camera")
    camera_only_mode = camera_feature_enabled and not rfid_feature_enabled

    context = {
        "scan_url": reverse("rfid-scan-next"),
        "table_mode": table_mode,
        "toggle_url": toggle_url,
        "toggle_label": toggle_label,
        "show_release_info": request.user.is_staff,
        "default_endianness": RFID.BIG_ENDIAN,
        "camera_enabled": camera_feature_enabled,
        "rfid_feature_enabled": rfid_feature_enabled,
        "camera_only_mode": camera_only_mode,
    }
    if request.user.is_staff:
        context["admin_change_url_template"] = reverse(
            "admin:cards_rfid_change", args=[0]
        )
        context["deep_read_url"] = reverse("rfid-scan-deep")
        context["admin_view_url"] = reverse("admin:cards_rfid_scan")
    return render(request, "cards/reader.html", context)


reader.required_features_any = frozenset({"rfid-scanner", "rpi-camera"})
