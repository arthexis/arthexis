import json

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from pages.utils import landing

from .scanner import scan_sources, restart_sources, test_sources, enable_deep_read_mode
from .reader import validate_rfid_value


@login_required(login_url="pages:login")
def scan_next(request):
    """Return the next scanned RFID tag or validate a client-provided value."""

    if request.method == "POST":
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Authentication required"}, status=401)
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({"error": "Invalid JSON payload"}, status=400)
        rfid = payload.get("rfid") or payload.get("value")
        kind = payload.get("kind")
        result = validate_rfid_value(rfid, kind=kind)
    else:
        result = scan_sources(request)
    status = 500 if result.get("error") else 200
    return JsonResponse(result, status=status)


@login_required(login_url="pages:login")
@require_POST
def scan_restart(_request):
    """Restart the RFID scanner."""
    result = restart_sources()
    status = 500 if result.get("error") else 200
    return JsonResponse(result, status=status)


@login_required(login_url="pages:login")
def scan_test(_request):
    """Report wiring information for the local RFID scanner."""
    result = test_sources()
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
@login_required(login_url="pages:login")
def reader(request):
    """Public page to scan RFID tags."""
    params = request.GET.copy()
    mode = params.get("mode")
    table_mode = mode == "table"
    params = params.copy()
    params._mutable = True
    if table_mode:
        params.pop("mode", None)
        toggle_label = "Switch to Single Mode"
    else:
        params["mode"] = "table"
        toggle_label = "Switch to Table Mode"
    toggle_query = params.urlencode()
    toggle_url = request.path
    if toggle_query:
        toggle_url = f"{toggle_url}?{toggle_query}"

    context = {
        "scan_url": reverse("rfid-scan-next"),
        "restart_url": reverse("rfid-scan-restart"),
        "test_url": reverse("rfid-scan-test"),
        "table_mode": table_mode,
        "toggle_url": toggle_url,
        "toggle_label": toggle_label,
        "show_release_info": False,
    }
    if request.user.is_staff:
        context["admin_change_url_template"] = reverse(
            "admin:core_rfid_change", args=[0]
        )
        context["deep_read_url"] = reverse("rfid-scan-deep")
    return render(request, "rfid/reader.html", context)
