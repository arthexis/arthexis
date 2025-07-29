from io import BytesIO
import base64
import qrcode
from django.shortcuts import render
from website.utils import landing


@landing("QR Generator")
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
    return render(request, "qrcodes/landing.html", {"img_src": img_src})

