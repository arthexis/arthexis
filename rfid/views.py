from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST
from website.utils import landing

from .background_reader import get_next_tag, start, stop


def scan_next(_request):
    """Return the next scanned RFID tag."""
    result = get_next_tag()
    if result and result.get("error"):
        return JsonResponse({"error": result["error"]}, status=500)
    if not result:
        result = {"rfid": None, "label_id": None}
    return JsonResponse(result)


@require_POST
def scan_restart(_request):
    """Restart the background RFID scanner."""
    stop()
    start()
    return JsonResponse({"status": "restarted"})


@landing("RFID Reader")
def reader(request):
    """Public page to read RFID tags."""
    context = {
        "scan_url": reverse("rfid-scan-next"),
        "restart_url": reverse("rfid-scan-restart"),
    }
    return render(request, "rfid/reader.html", context)
