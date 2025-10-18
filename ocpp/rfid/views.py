import json

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.contrib.auth.views import redirect_to_login
from django.contrib.admin.views.decorators import staff_member_required
from nodes.models import Node
from pages.utils import landing

from .scanner import scan_sources, enable_deep_read_mode
from .reader import validate_rfid_value
from core.models import RFID
from .utils import build_mode_toggle


def _request_wants_json(request):
    """Return True if the request expects a JSON response."""

    accept = request.headers.get("accept", "")
    if "application/json" in accept.lower():
        return True
    # Fallback for older callers that mark AJAX requests without Accept headers.
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def scan_next(request):
    """Return the next scanned RFID tag or validate a client-provided value."""

    node = Node.get_local()
    role_name = node.role.name if node and node.role else ""
    allow_anonymous = role_name == "Control"

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
        result = scan_sources(request, endianness=endianness)
    status = 500 if result.get("error") else 200
    return JsonResponse(result, status=status)


@require_POST
@staff_member_required
def scan_deep(_request):
    """Enable deep read mode on the RFID scanner."""
    result = enable_deep_read_mode()
    status = 500 if result.get("error") else 200
    return JsonResponse(result, status=status)


@landing("RFID Tag Validator")
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

    context = {
        "scan_url": reverse("rfid-scan-next"),
        "table_mode": table_mode,
        "toggle_url": toggle_url,
        "toggle_label": toggle_label,
        "show_release_info": request.user.is_staff,
        "default_endianness": RFID.BIG_ENDIAN,
    }
    if request.user.is_staff:
        context["admin_change_url_template"] = reverse(
            "admin:core_rfid_change", args=[0]
        )
        context["deep_read_url"] = reverse("rfid-scan-deep")
        context["admin_view_url"] = reverse("admin:core_rfid_scan")
    return render(request, "rfid/reader.html", context)
