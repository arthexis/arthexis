"""Template tags for the RFID app."""

import base64
from io import BytesIO

import qrcode
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag(takes_context=True)
def current_page_qr(context, size=200):
    """Return an <img> tag with a QR code for the current page."""
    request = context.get("request")
    if request is None:
        return ""
    url = request.build_absolute_uri()
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    img_src = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    return mark_safe(
        f'<img src="{img_src}" alt="QR code for this page" width="{size}" height="{size}">'  # noqa: E501
    )
