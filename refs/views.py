from datetime import timedelta
from io import BytesIO

import base64
import qrcode
from django.shortcuts import render
from django.utils import timezone

from website.utils import landing
from .models import Reference


@landing("Recent References")
def recent(request):
    """Display references created in the last 72 hours."""
    since = timezone.now() - timedelta(hours=72)
    refs_qs = Reference.objects.filter(created__gte=since).order_by("-created")
    return render(request, "refs/recent.html", {"references": refs_qs})


@landing("Reference Generator")
def generator(request):
    """Landing page with a form to generate QR codes without storing them."""
    data = request.GET.get("data")
    img_src = None
    if data:
        qr = qrcode.QRCode(box_size=10, border=4)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        img_src = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    return render(request, "refs/landing.html", {"img_src": img_src})

