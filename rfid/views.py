from django.http import JsonResponse
from django.shortcuts import render
from website.utils import landing
from django.urls import reverse

from .background_reader import get_next_tag


def scan_next(_request):
    """Return the next scanned RFID tag."""
    result = get_next_tag()
    if result and result.get("error"):
        return JsonResponse({"error": result["error"]}, status=500)
    if not result:
        result = {"rfid": None, "label_id": None}
    return JsonResponse(result)


@landing("RFID Reader")
def reader(request):
    """Public page to read RFID tags."""
    context = {"scan_url": reverse("rfid-scan-next")}
    return render(request, "rfid/reader.html", context)
