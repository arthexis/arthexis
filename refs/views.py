from datetime import timedelta
from io import BytesIO

import base64
import qrcode
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone

from website.utils import landing
from .models import Reference
from .forms import ReferenceForm


@landing("Recent References")
def recent(request):
    """Display recent references and allow creating new ones."""
    since = timezone.now() - timedelta(hours=72)
    refs_qs = Reference.objects.filter(created__gte=since).order_by("-created")

    if request.method == "POST":
        if not request.user.is_authenticated:
            return HttpResponseForbidden("Authentication required")
        form = ReferenceForm(request.POST, request.FILES)
        if form.is_valid():
            ref = form.save(commit=False)
            ref.author = request.user
            ref.save()
            return redirect("refs:recent")
    else:
        form = ReferenceForm() if request.user.is_authenticated else None

    return render(
        request,
        "refs/recent.html",
        {"references": refs_qs, "form": form},
    )


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

