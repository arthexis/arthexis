from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from website.utils import landing

from .scanner import scan_sources


def scan_next(_request):
    """Return the next scanned RFID tag."""
    result = scan_sources()
    status = 500 if result.get("error") else 200
    return JsonResponse(result, status=status)


@landing("RFID Reader")
def reader(request):
    """Public page to read RFID tags."""
    context = {
        "scan_url": reverse("rfid-scan-next"),
    }
    return render(request, "rfid/reader.html", context)
