from django.shortcuts import render
from website.utils import landing


@landing("RFID Reader")
def reader(request):
    """Public page to read RFID tags."""
    return render(request, "rfid/reader.html")
