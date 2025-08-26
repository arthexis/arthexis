from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from website.utils import landing

from accounts.models import RFID

from .scanner import scan_sources, restart_sources, test_sources


def scan_next(request):
    """Return the next scanned RFID tag."""
    result = scan_sources(request)
    status = 500 if result.get("error") else 200
    return JsonResponse(result, status=status)


@require_POST
def scan_restart(_request):
    """Restart the RFID scanner."""
    result = restart_sources()
    status = 500 if result.get("error") else 200
    return JsonResponse(result, status=status)


def scan_test(_request):
    """Report wiring information for the local RFID scanner."""
    result = test_sources()
    status = 500 if result.get("error") else 200
    return JsonResponse(result, status=status)


@landing("RFID Reader")
def reader(request):
    """Public page to read RFID tags."""
    context = {
        "scan_url": reverse("rfid-scan-next"),
        "restart_url": reverse("rfid-scan-restart"),
        "test_url": reverse("rfid-scan-test"),
    }
    return render(request, "rfid/reader.html", context)


def label(request, label_id):
    """Public page for a single RFID label."""
    tag = get_object_or_404(RFID, label_id=label_id)
    return render(request, "rfid/label.html", {"rfid": tag})
